"""基本面 PIT 同步（基于 baostock 估值字段）。

baostock 的 query_daily_history_k_AStock 自带 peTTM/pbMRQ/psTTM/pcfNcfTTM，
按日频存入 fundamental_pit 表，查询时按 trade_date <= 查询日 PIT 查询。
"""
import asyncio
import logging
from datetime import date
from typing import Optional

import pandas as pd
from sqlalchemy import and_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.database import async_session
from app.models.fundamental import FundamentalPIT
from app.services.data.baostock_client import (
    fetch_daily_all_a_stock_sync,
    from_baostock_code,
)

logger = logging.getLogger(__name__)


async def sync_fundamental_pit(trade_date: str) -> dict:
    """同步某日全市场基本面 PIT 数据。

    Args:
        trade_date: 'YYYY-MM-DD'
    Returns:
        {"date": trade_date, "total": N, "inserted": M, "skipped": K}
    """
    # 在线程池拉数据，避免阻塞事件循环
    loop = asyncio.get_event_loop()
    df = await loop.run_in_executor(None, fetch_daily_all_a_stock_sync, trade_date)

    if df.empty:
        return {"date": trade_date, "total": 0, "inserted": 0, "skipped": 0}

    # 转换：baostock 代码 → QLib 代码；估值字段强制数值化
    df["code"] = df["code"].apply(from_baostock_code)  # sh.600000 → sh600000
    for col in ["peTTM", "pbMRQ", "psTTM", "pcfNcfTTM"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["trade_date"] = pd.to_datetime(df["date"]).dt.date

    # 选列 + 重命名为表字段
    records = df[["code", "trade_date", "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM"]].copy()
    records.columns = ["code", "trade_date", "pe_ttm", "pb_mrq", "ps_ttm", "pcf_ncf_ttm"]
    # 转 object 以便 None 不被 float dtype 转回 nan（SQLite 写入需 NULL 而非 NaN）
    records = records.astype(object).where(pd.notna(records), None)

    async with async_session() as session:
        # 查已存在的 code+trade_date（按当日过滤，幂等：同 code+trade_date 跳过）
        existing = await session.execute(
            select(FundamentalPIT.code, FundamentalPIT.trade_date).where(
                FundamentalPIT.trade_date == records["trade_date"].iloc[0]
            )
        )
        existing_keys = {(r[0], r[1]) for r in existing.fetchall()}

        # 仅插新记录
        to_insert = [
            r
            for r in records.to_dict("records")
            if (r["code"], r["trade_date"]) not in existing_keys
        ]

        if to_insert:
            await session.execute(sqlite_insert(FundamentalPIT.__table__).values(to_insert))
            await session.commit()

        return {
            "date": trade_date,
            "total": len(records),
            "inserted": len(to_insert),
            "skipped": len(records) - len(to_insert),
        }


async def query_fundamental_pit(code: str, trade_date: date) -> Optional[dict]:
    """PIT 查询：返回 <= trade_date 的最近一条基本面数据。

    Args:
        code: QLib 代码 'sh600000'
        trade_date: 查询日期
    Returns:
        {"pe_ttm":..., "pb_mrq":..., "ps_ttm":..., "pcf_ncf_ttm":..., "trade_date":...} 或 None
    """
    async with async_session() as session:
        result = await session.execute(
            select(FundamentalPIT)
            .where(
                and_(
                    FundamentalPIT.code == code,
                    FundamentalPIT.trade_date <= trade_date,
                )
            )
            .order_by(FundamentalPIT.trade_date.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row:
            return {
                "pe_ttm": row.pe_ttm,
                "pb_mrq": row.pb_mrq,
                "ps_ttm": row.ps_ttm,
                "pcf_ncf_ttm": row.pcf_ncf_ttm,
                "trade_date": row.trade_date,
            }
        return None
