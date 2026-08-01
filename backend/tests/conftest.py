"""pytest 全局 fixture。

Postgres 适配：
- 默认 DATABASE_URL 由环境变量提供（CI: postgres:5432；本地：用户自配）
- session 级 fixture 启动时建表，结束时 drop_all，避免污染共享库
- 各测试用 TRUNCATE 隔离数据
- 无 DATABASE_URL 或 DB 不可达时所有 DB 相关测试自动跳过
"""

import os
import struct
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

# 全局 flag：DB 是否真正可用（由 _create_tables 设置）
_DB_REALLY_AVAILABLE = False


@pytest.fixture(scope="session")
def _db_available() -> bool:
    """检查 DATABASE_URL 是否设置（不实际连接，避免创建临时 event loop）。"""
    return bool(os.getenv("DATABASE_URL", "").strip())


@pytest.fixture(scope="session", autouse=True)
async def _create_tables(_db_available):
    """session 级：建表 -> 测试 -> drop_all。

    如果 DATABASE_URL 设置但 DB 不可达，设 _DB_REALLY_AVAILABLE=False，
    后续 _truncate_tables 跳过，不影响不依赖 DB 的测试。
    """
    global _DB_REALLY_AVAILABLE
    if not _db_available:
        yield
        return
    import app.models  # noqa: F401  # 触发所有模型注册到 Base.metadata
    from app.core.database import Base, engine

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        _DB_REALLY_AVAILABLE = True
    except Exception:
        _DB_REALLY_AVAILABLE = False
    yield
    if _DB_REALLY_AVAILABLE:
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
        except Exception:
            pass
        await engine.dispose()


@pytest.fixture(autouse=True)
async def _truncate_tables(_db_available):
    """每个测试后：清空所有表。DB 不可用或表不存在时静默跳过。"""
    if not _db_available or not _DB_REALLY_AVAILABLE:
        yield
        return
    yield
    from app.core.database import Base, engine

    try:
        async with engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                await conn.execute(table.delete())
    except Exception:
        pass


@pytest.fixture(scope="session")
def db_ready() -> bool:
    """供非 autouse 测试引用：返回 DB 是否真正可用（建表成功）。"""
    return _DB_REALLY_AVAILABLE


@pytest.fixture
def tmp_qlib_data(tmp_path):
    """创建临时 qlib bin 数据用于测试（mock 小型 bin 文件）。"""
    base = tmp_path / "qlib_test"
    cal_dir = base / "calendars"
    feat_dir = base / "features"
    cal_dir.mkdir(parents=True)
    feat_dir.mkdir(parents=True)

    dates = [f"2024-01-{i:02d}" for i in range(1, 11)]
    with open(cal_dir / "day.txt", "w") as f:
        for d in dates:
            f.write(d + "\n")

    stocks = ["sh600000", "sz000001", "sz300001"]
    for stock in stocks:
        sdir = feat_dir / stock
        sdir.mkdir()
        for field in ["open", "high", "low", "close", "volume"]:
            data = np.abs(np.random.randn(10).astype(np.float32) * 10 + 50)
            if field == "volume":
                data = data * 100000
            with open(sdir / f"{field}.day.bin", "wb") as f:
                f.write(struct.pack("<f", 0.0))  # start_index
            data.tofile(f)

    return base


@pytest.fixture
def mock_db_session():
    """Mock 数据库 session。"""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture
def tmp_workdir(tmp_path):
    """临时工作目录。"""
    return tmp_path
