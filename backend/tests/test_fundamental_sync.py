"""fundamental_sync 单元测试。

用临时 SQLite 文件 + mock baostock，验证：
- sync_fundamental_pit 幂等（同日两次同步，第二次全 skipped）
- query_fundamental_pit PIT 语义（查早于最早记录的日期返回 None；
  查中间日期返回 <= 查询日的最近一条）
"""
from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest
from sqlalchemy import create_engine as sync_create_engine
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.fundamental import FundamentalPIT
from app.services.data import fundamental_sync


@pytest.fixture
def memory_db(tmp_path):
    """临时 SQLite 文件，patch async_session，仅建 fundamental_pit 表。

    用同步引擎建表（同一文件，异步会话共享），避免 async fixture 的复杂度。
    每个 test 独立 tmp_path，互不污染。
    """
    db_file = tmp_path / "test_fundamental.db"
    # 同步引擎建表（同文件，供异步会话读写）
    sync_engine = sync_create_engine(f"sqlite:///{db_file}")
    FundamentalPIT.__table__.create(sync_engine, checkfirst=True)
    sync_engine.dispose()

    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    Session = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    with patch.object(fundamental_sync, "async_session", Session):
        yield Session


def _mock_fetch(trade_date):
    """构造 3 只股票的 mock 日行情（含估值字段，含 None 缺失值）。"""
    return pd.DataFrame(
        {
            "date": [trade_date] * 3,
            "code": ["sh.600000", "sz.000001", "sz.300001"],
            "peTTM": [10.5, 20.0, None],
            "pbMRQ": [1.2, 3.5, 0.8],
            "psTTM": [5.0, 8.0, 2.0],
            "pcfNcfTTM": [15.0, None, 25.0],
        }
    )


async def test_sync_idempotent(memory_db):
    """同日两次同步：第一次全 inserted，第二次全 skipped，库内仅 3 条。"""
    with patch.object(fundamental_sync, "fetch_daily_all_a_stock_sync", _mock_fetch):
        r1 = await fundamental_sync.sync_fundamental_pit("2024-01-15")
    assert r1["total"] == 3
    assert r1["inserted"] == 3
    assert r1["skipped"] == 0

    with patch.object(fundamental_sync, "fetch_daily_all_a_stock_sync", _mock_fetch):
        r2 = await fundamental_sync.sync_fundamental_pit("2024-01-15")
    assert r2["total"] == 3
    assert r2["inserted"] == 0
    assert r2["skipped"] == 3

    # 库内仍只有 3 条（未重复写入）
    async with memory_db() as session:
        cnt = await session.scalar(
            select(func.count()).select_from(FundamentalPIT.__table__)
        )
    assert cnt == 3


async def test_pit_query_before_earliest_returns_none(memory_db):
    """查早于最早记录的日期 → None。"""
    with patch.object(fundamental_sync, "fetch_daily_all_a_stock_sync", _mock_fetch):
        await fundamental_sync.sync_fundamental_pit("2024-01-15")

    result = await fundamental_sync.query_fundamental_pit("sh600000", date(2024, 1, 14))
    assert result is None


async def test_pit_query_returns_nearest(memory_db):
    """查中间日期 → 返回 <= 查询日的最近一条（PIT 语义）。"""
    def _fetch_d1(d):
        return pd.DataFrame(
            {
                "date": [d],
                "code": ["sh.600000"],
                "peTTM": [10.0],
                "pbMRQ": [1.0],
                "psTTM": [2.0],
                "pcfNcfTTM": [3.0],
            }
        )

    def _fetch_d2(d):
        return pd.DataFrame(
            {
                "date": [d],
                "code": ["sh.600000"],
                "peTTM": [99.0],
                "pbMRQ": [9.0],
                "psTTM": [8.0],
                "pcfNcfTTM": [7.0],
            }
        )

    with patch.object(fundamental_sync, "fetch_daily_all_a_stock_sync", _fetch_d1):
        await fundamental_sync.sync_fundamental_pit("2024-01-15")
    with patch.object(fundamental_sync, "fetch_daily_all_a_stock_sync", _fetch_d2):
        await fundamental_sync.sync_fundamental_pit("2024-01-20")

    # 查 1/18 → 应回 1/15 的记录（pe_ttm=10.0）
    r = await fundamental_sync.query_fundamental_pit("sh600000", date(2024, 1, 18))
    assert r is not None
    assert r["pe_ttm"] == 10.0
    assert r["trade_date"] == date(2024, 1, 15)

    # 查 1/25 → 应回 1/20 的记录（pe_ttm=99.0）
    r2 = await fundamental_sync.query_fundamental_pit("sh600000", date(2024, 1, 25))
    assert r2 is not None
    assert r2["pe_ttm"] == 99.0
    assert r2["trade_date"] == date(2024, 1, 20)
