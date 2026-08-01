"""Alembic env.py：Postgres 适配。

数据库 URL 解析顺序：
1. 环境变量 DATABASE_URL（含 postgresql+asyncpg 前缀时转为 postgresql+psycopg 同步驱动）
2. 由 POSTGRES_* 环境变量构造
3. 兜底从 app.core.config 推断
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app.models  # noqa: F401, E402  # 触发所有模型注册到 Base.metadata
from app.core.database import Base  # noqa: E402

target_metadata = Base.metadata


def _resolve_sync_url() -> str:
    """Alembic 走同步驱动：从 DATABASE_URL 转 asyncpg -> psycopg2/psycopg。"""
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        # asyncpg -> psycopg（alembic 同步）
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
    user = os.getenv("POSTGRES_USER", "quantlab")
    password = os.getenv("POSTGRES_PASSWORD", "quantlab")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "quantlab")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"


config.set_main_option("sqlalchemy.url", _resolve_sync_url())


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连接数据库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接到数据库执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
