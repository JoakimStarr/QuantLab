"""数据库引擎与连接管理（Postgres 统一栈）。

DATABASE_URL 通过环境变量配置，默认 `postgresql+asyncpg://quantlab:quantlab@localhost:5432/quantlab`。
docker-compose / CI / 本地都使用同一连接格式，避免多套代码路径。
"""

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)


def _resolve_database_url() -> str:
    """解析数据库 URL：优先 DATABASE_URL 环境变量，否则用 config + 默认值。

    优先级：DATABASE_URL env > 自动构造的本地默认
    """
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        return url
    # 默认 Postgres：用户名/密码/库名取自 settings.data（向后兼容配置）
    user = os.getenv("POSTGRES_USER", "quantlab")
    password = os.getenv("POSTGRES_PASSWORD", "quantlab")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "quantlab")
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"


DATABASE_URL = _resolve_database_url()

# Postgres 连接池调优：
# - pool_size / max_overflow: 固定池 + 溢出，连接复用避免反复 TLS 握手
# - pool_pre_ping: 健康检查，剔除已被服务端断开的失效连接
# - pool_recycle: 1h 主动回收，避免 Postgres 默认 idle_timeout (2h) 后连接僵死
# - pool_timeout: 30s 等不到连接就报错，避免请求无限堆积
# - pool_use_lifo: 后进先出，热点连接优先复用，冷连接自然淘汰
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "20")),
    pool_pre_ping=True,
    pool_recycle=int(os.getenv("DB_POOL_RECYCLE", "3600")),
    pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "30")),
    pool_use_lifo=True,
)

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """SQLAlchemy ORM 基类。"""


async def init_db() -> None:
    """启动时初始化数据库：建表 + Alembic 迁移。

    顺序：
    1. import app.models：触发所有 ORM 模型注册到 Base.metadata
    2. create_all：建尚未存在的表
    3. Alembic upgrade head：处理已有表的列变更
    """
    import app.models  # noqa: F401  # 触发所有模型注册

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        await asyncio.to_thread(_run_alembic_upgrade)
    except Exception as e:
        logger.warning("Alembic 迁移跳过: %s", e)


def _run_alembic_upgrade() -> None:
    """在子线程同步执行 alembic upgrade head。"""
    backend_root = Path(__file__).resolve().parent.parent.parent
    ini = backend_root / "alembic.ini"
    if not ini.exists():
        return
    env = os.environ.copy()
    # 把当前 DATABASE_URL 透传给 alembic 子进程
    env["DATABASE_URL"] = DATABASE_URL
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini), "upgrade", "head"],
        check=False,
        cwd=str(backend_root),
        capture_output=True,
        timeout=60,
        env=env,
    )


async def get_db():
    """FastAPI 依赖：每次请求拿一个 session。"""
    async with async_session() as session:
        yield session


async def health_check() -> dict:
    """健康检查：尝试连接 + SELECT 1，返回状态字典。"""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def shutdown_db() -> None:
    """应用关闭时清理连接池。"""
    await engine.dispose()
