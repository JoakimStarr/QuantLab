"""PIT（Point-In-Time）市值：用历史可得数据反推每日总市值，供因子中性化。

背景（A4）：旧实现用 akshare **实时快照**市值套到全部历史截面，造成前视偏差
（用今天的市值解释 5 年前的收益）。本模块改为逐日 PIT 市值：

    总市值(t, stock) = pe_ttm(t, stock) × 净利润(t, stock)

- ``pe_ttm``：``stock_daily.pe_ttm``（baostock 按交易日，已落库）。
- 净利润：``financial_indicator`` 里 ``field_name='netprofit'`` 的归母净利润，
  按 ``available_date``（真实公告日/法定截止日）前向填充到交易日，保证 t 日只用
  t 之前已公告的数据。

注：akshare 归母净利润按期（季）披露，PE_ttm 用的是 TTM，二者口径不完全一致，
因此该市值是**量级正确**的截面规模代理，用于对数市值中性化已足够；不追求与市值
绝对值一致（中性化回归的对数尺度对整体常数不敏感）。

无流通/总股本字段，故用 pe×净利润反推，而非直接取市值。
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from cachetools import LRUCache

logger = logging.getLogger(__name__)

# PIT 市值缓存：key=md5(start|end|codes)，value=log 市值 Series
_PIT_CACHE: LRUCache = LRUCache(maxsize=8)

_SYNC_ENGINE = None
_CHUNK = 800  # code IN 列表分块，避免超参数上限


def _get_sync_engine():
    """惰性创建同步 psycopg 引擎（neutralize 同步调用，不能走 asyncpg 事件循环）。

    导入期不建连；DB 不可用时首次查询抛错，由上层门禁捕获后跳过中性化。
    """
    global _SYNC_ENGINE
    if _SYNC_ENGINE is None:
        from sqlalchemy import create_engine

        from app.core.database import DATABASE_URL
        url = DATABASE_URL
        if url.startswith("postgresql+asyncpg://"):
            url = url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
        _SYNC_ENGINE = create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=2)
    return _SYNC_ENGINE


def build_pit_log_market_cap(pe_ttm: pd.DataFrame,
                             netprofit_pit: pd.DataFrame) -> pd.Series:
    """纯函数：pe_ttm × 净利润(PIT) → log 市值，返回 MultiIndex (datetime, instrument)。

    Args:
        pe_ttm: 宽表，index=交易日(DatetimeIndex), columns=instrument（小写 qlib 代码）
        netprofit_pit: 宽表，同 shape，净利润已按 available_date 前向填充到交易日

    Returns:
        pd.Series，index=(datetime, instrument)，值=ln(市值)。数据不足返回空 Series。
        pe/净利润非正的样本被剔除（亏损股无法用 pe 反推正市值）。
    """
    if pe_ttm is None or pe_ttm.empty or netprofit_pit is None or netprofit_pit.empty:
        return pd.Series(dtype=float)
    cols = pe_ttm.columns.intersection(netprofit_pit.columns)
    dates = pe_ttm.index.intersection(netprofit_pit.index)
    if len(cols) == 0 or len(dates) == 0:
        return pd.Series(dtype=float)
    pe = pe_ttm.loc[dates, cols].astype(float)
    npf = netprofit_pit.loc[dates, cols].astype(float)
    mcap = (pe * npf).where((pe > 0) & (npf > 0))
    ln_mcap = np.log(mcap)
    series = ln_mcap.stack(dropna=True)
    if series.empty:
        return pd.Series(dtype=float)
    series.index = series.index.set_names(["datetime", "instrument"])
    return series.sort_index()


def _query_pe(engine, codes: list[str], start, end) -> pd.DataFrame:
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.models.baostock import StockDaily

    frames = []
    with Session(engine) as session:
        for i in range(0, len(codes), _CHUNK):
            chunk = codes[i:i + _CHUNK]
            stmt = (
                select(StockDaily.code, StockDaily.trade_date, StockDaily.pe_ttm)
                .where(
                    StockDaily.code.in_(chunk),
                    StockDaily.trade_date >= start,
                    StockDaily.trade_date <= end,
                    StockDaily.pe_ttm.isnot(None),
                )
            )
            rows = session.execute(stmt).all()
            if rows:
                frames.append(pd.DataFrame(rows, columns=["code", "trade_date", "pe_ttm"]))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["instrument"] = df["code"].str.lower()
    wide = df.pivot_table(index="trade_date", columns="instrument",
                          values="pe_ttm", aggfunc="last")
    wide.index = pd.to_datetime(wide.index)
    return wide.sort_index()


def _query_netprofit(engine, codes: list[str], end) -> pd.DataFrame:
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.models.fundamental import FinancialIndicator

    frames = []
    with Session(engine) as session:
        for i in range(0, len(codes), _CHUNK):
            chunk = codes[i:i + _CHUNK]
            stmt = (
                select(FinancialIndicator.code,
                       FinancialIndicator.available_date,
                       FinancialIndicator.value)
                .where(
                    FinancialIndicator.code.in_(chunk),
                    FinancialIndicator.field_name == "netprofit",
                    FinancialIndicator.available_date.isnot(None),
                    FinancialIndicator.available_date <= end,
                )
            )
            rows = session.execute(stmt).all()
            if rows:
                frames.append(pd.DataFrame(rows, columns=["code", "available_date", "value"]))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df = df.dropna(subset=["available_date", "value"])
    if df.empty:
        return pd.DataFrame()
    df["instrument"] = df["code"].str.lower()
    wide = df.pivot_table(index="available_date", columns="instrument",
                          values="value", aggfunc="last")
    wide.index = pd.to_datetime(wide.index)
    return wide.sort_index()


def load_pit_log_market_cap(factor_df: pd.DataFrame) -> pd.Series:
    """同步加载 factor_df 覆盖的 (datetime, instrument) 的 PIT log 市值。

    仅取 factor_df 出现过的股票与日期范围，控制查询量；结果 LRU 缓存。
    DB 不可用/无数据时返回空 Series（由调用方门禁跳过中性化，绝不回退实时快照）。

    Returns:
        pd.Series index=(datetime, instrument)，或空 Series。
    """
    if factor_df is None or factor_df.empty:
        return pd.Series(dtype=float)
    idx = factor_df.index
    try:
        dts = pd.to_datetime(idx.get_level_values("datetime"))
        instruments = idx.get_level_values("instrument").astype(str)
    except (KeyError, ValueError):
        logger.warning("PIT 市值：factor_df 索引缺少 datetime/instrument 层级，跳过")
        return pd.Series(dtype=float)

    start, end = dts.min().date(), dts.max().date()
    codes = sorted({c.upper() for c in instruments.unique()})
    if not codes:
        return pd.Series(dtype=float)

    import hashlib
    cache_key = hashlib.md5(
        f"{start}|{end}|{','.join(codes)}".encode()
    ).hexdigest()
    cached = _PIT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    try:
        engine = _get_sync_engine()
        pe_wide = _query_pe(engine, codes, start, end)
        np_wide = _query_netprofit(engine, codes, end)
    except Exception as e:  # noqa: BLE001
        logger.warning("PIT 市值加载失败（跳过中性化，不回退快照）: %s", str(e)[:160])
        return pd.Series(dtype=float)

    if pe_wide.empty or np_wide.empty:
        logger.warning("PIT 市值数据缺失（pe_ttm=%d 列, netprofit=%d 列），跳过中性化",
                       pe_wide.shape[1] if not pe_wide.empty else 0,
                       np_wide.shape[1] if not np_wide.empty else 0)
        return pd.Series(dtype=float)

    # 净利润按 available_date 前向填充到交易日轴
    trade_dates = pe_wide.index
    np_pit = (
        np_wide.reindex(np_wide.index.union(trade_dates))
        .ffill()
        .reindex(trade_dates)
    )
    series = build_pit_log_market_cap(pe_wide, np_pit)
    _PIT_CACHE[cache_key] = series
    logger.info("PIT 市值构建完成: %d 个 (date,stock)", len(series))
    return series


def clear_pit_cache() -> None:
    """清空 PIT 市值缓存（测试/重置用）。"""
    _PIT_CACHE.clear()
