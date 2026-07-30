from pathlib import Path
import os
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import settings

DATABASE_URL = f"sqlite+aiosqlite:///{settings.db_path}"

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    """SQLite PRAGMA 是 per-connection 设置，需在每个新连接上执行。"""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.execute("PRAGMA busy_timeout = 5000")  # 写冲突时等待 5s 而非立即报 locked
    cursor.execute("PRAGMA synchronous = NORMAL")  # WAL 下 NORMAL 兼顾安全与性能
    cursor.execute("PRAGMA cache_size = -65536")
    cursor.close()


async def init_db():
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        # 创建尚未存在的表（新模型如 TaskResult 会自动建表）。
        await conn.run_sync(Base.metadata.create_all)
    # 老表的列变更由 Alembic 迁移管理；失败仅告警不阻断启动（测试/无 alembic 环境）
    try:
        import asyncio
        await asyncio.to_thread(_run_alembic_upgrade)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Alembic 迁移跳过: %s", e)


def _run_alembic_upgrade() -> None:
    """在子线程同步执行 alembic upgrade head。"""
    from pathlib import Path
    import subprocess
    import sys
    backend_root = Path(__file__).resolve().parent.parent.parent
    ini = backend_root / "alembic.ini"
    if not ini.exists():
        return
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini), "upgrade", "head"],
        check=False,
        cwd=str(backend_root),
        capture_output=True,
        timeout=60,
    )


async def get_db():
    async with async_session() as session:
        yield session