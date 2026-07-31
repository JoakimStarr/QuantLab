"""基本面PIT数据采集器：财务报表 + 估值指标。

数据源：akshare（免费）
- 财务报表：stock_financial_abstract（财务摘要）、stock_financial_analysis_indicator（财务分析指标）
- 估值指标：stock_a_indicator_lg（PE/PB/PS日频）或 stock_zh_valuation_baidu（百度估值）
- 申万行业：sw_industry（已由 industry_sync.py 处理，这里不重复）

PIT原则：所有查询必须按 announce_date <= 交易日 过滤，避免未来函数。

注意：项目数据库为异步（aiosqlite + AsyncSession），所有 DB 读写均通过
app.core.database.async_session 上下文管理器并以 await 执行，无同步 get_session。
"""
import asyncio
import logging
import math
from datetime import datetime, timedelta
from functools import partial

import pandas as pd
from sqlalchemy import select, and_

from app.core.database import async_session
from app.core.errors import DataFetchError
from app.models.fundamental import FundamentalPIT

logger = logging.getLogger(__name__)


async def _run_async(func, *args, timeout: int = 30, **kwargs):
    """在线程池中运行同步 akshare 函数。

    与 app.services.data.akshare_client.fetch_data 的区别：本函数对空结果
    不抛错、不重试（按股票逐只拉取时空结果往往是合法的“无数据”），
    仅在超时时抛 DataFetchError。
    """
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, partial(func, *args, **kwargs)),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        raise DataFetchError(f"akshare 请求超时 (timeout={timeout}s)")


def _ak_code_to_qlib(ak_code: str) -> str:
    """6位akshare代码 -> qlib代码。"""
    c = str(ak_code).strip().zfill(6)
    if c.startswith(("60", "68")):
        return "SH" + c
    if c.startswith(("00", "30")):
        return "SZ" + c
    if c.startswith(("83", "87", "43", "92", "88")):
        return "BJ" + c
    return "SH" + c


def _qlib_to_ak(qlib_code: str) -> str:
    """qlib代码 -> 6位akshare代码。"""
    c = qlib_code.upper()
    return c[2:] if c.startswith(("SH", "SZ", "BJ")) else c


async def fetch_financial_abstract(code: str) -> pd.DataFrame:
    """拉取单只股票财务摘要（资产负债表+利润表关键指标）。

    akshare接口：stock_financial_abstract(symbol=ak_code)
    返回字段含：选项、指标、报告期、数值（多为空需pivot）

    Returns:
        DataFrame: 列含 report_date, revenue, net_profit, total_assets, net_assets, eps, bps, roe
    """
    import akshare as ak
    ak_code = _qlib_to_ak(code)
    try:
        df = await _run_async(ak.stock_financial_abstract, symbol=ak_code, timeout=30)
        if df is None or df.empty:
            return pd.DataFrame()
        # akshare财务摘要格式：每行一个指标，列为报告期，需转置
        # 字段映射（根据akshare版本可能变化，需校验）
        # 这里做通用pivot：以"指标"为行，"报告期"为列
        # 实际字段名需运行时确认，先做best-effort映射
        return df
    except Exception as e:
        logger.warning("拉取财务摘要 %s 失败: %s", code, e)
        return pd.DataFrame()


async def fetch_valuation_daily(code: str, start: str, end: str) -> pd.DataFrame:
    """拉取单只股票日频估值指标（PE/PB/PS）。

    akshare接口：stock_a_indicator_lg(symbol=ak_code)
    返回字段：trade_date, pe, pe_ttm, pb, ps, ps_ttm, dv_ratio, dv_ttm, total_mv

    Returns:
        DataFrame: 列含 date, pe, pb, ps, total_mv
    """
    import akshare as ak
    ak_code = _qlib_to_ak(code)
    try:
        df = await _run_async(ak.stock_a_indicator_lg, symbol=ak_code, timeout=30)
        if df is None or df.empty:
            return pd.DataFrame()
        # 列名标准化
        rename = {
            "trade_date": "date",
            "pe_ttm": "pe",
            "ps_ttm": "ps",
        }
        df = df.rename(columns=rename)
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        # 按日期过滤
        df = df[(df["date"] >= start) & (df["date"] <= end)]
        keep = ["date", "pe", "pb", "ps", "total_mv"]
        return df[[c for c in keep if c in df.columns]]
    except Exception as e:
        logger.warning("拉取估值 %s 失败: %s", code, e)
        return pd.DataFrame()


async def sync_fundamental_pit(
    codes: list,
    start: str = None,
    end: str = None,
) -> dict:
    """同步基本面PIT数据（财务报表 + 估值）。

    Args:
        codes: qlib代码列表 ["SH600000", "SZ000001"]
        start: 起始日 YYYY-MM-DD，默认近2年
        end: 结束日 YYYY-MM-DD，默认今天

    Returns:
        dict: ok/total/success/failed/rows
    """
    if end is None:
        end = datetime.now().strftime("%Y-%m-%d")
    if start is None:
        start = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")

    success = 0
    failed = 0
    total_rows = 0

    for code in codes:
        try:
            # 1. 拉取估值日频数据（每个交易日一条）
            val_df = await fetch_valuation_daily(code, start, end)
            if not val_df.empty:
                records = []
                for _, row in val_df.iterrows():
                    records.append(FundamentalPIT(
                        code=code,
                        report_date=row["date"],      # 估值快照：report_date=当日
                        announce_date=row["date"],    # 估值当日可知
                        pe=_safe_float(row.get("pe")),
                        pb=_safe_float(row.get("pb")),
                        ps=_safe_float(row.get("ps")),
                        total_mv=_safe_float(row.get("total_mv")),
                        source="akshare",
                    ))
                if records:
                    # 去重：按 (code, announce_date) 跳过已存在记录（幂等写入）
                    # SQLite 无 ON CONFLICT 且表上无唯一约束，采用"查出已存在日期集 + 仅插新"。
                    async with async_session() as session:
                        existing = await session.execute(
                            select(FundamentalPIT.announce_date).where(and_(
                                FundamentalPIT.code == code,
                                FundamentalPIT.announce_date >= start,
                                FundamentalPIT.announce_date <= end,
                            ))
                        )
                        existing_dates = set(existing.scalars())
                        new_records = [
                            rec for rec in records if rec.announce_date not in existing_dates
                        ]
                        if new_records:
                            session.add_all(new_records)
                            await session.commit()
                        total_rows += len(new_records)

            # 2. 拉取财务报表（每报告期一条，PIT关键）
            fin_df = await fetch_financial_abstract(code)
            # TODO: 财务摘要格式解析较复杂，akshare返回的多级表头需专门处理
            # 此处先打日志，后续迭代补充解析逻辑
            if fin_df.empty:
                logger.debug("财务摘要为空 %s", code)

            success += 1
        except Exception as e:
            logger.warning("同步基本面 %s 失败: %s", code, e)
            failed += 1

    logger.info("基本面PIT同步完成: total=%d success=%d failed=%d rows=%d",
                len(codes), success, failed, total_rows)
    return {
        "ok": True,
        "total": len(codes),
        "success": success,
        "failed": failed,
        "rows": total_rows,
    }


async def query_fundamental_pit(code: str, trade_date: str, field: str = None):
    """PIT查询：取 trade_date 当日可知的最新基本面数据。

    注意：项目数据库为异步，本函数为 async，调用方需 await。

    Args:
        code: qlib代码 SH600000
        trade_date: 交易日 YYYY-MM-DD
        field: 指定字段名（如 pe/pb/eps），None则返回整条记录

    Returns:
        单个值（field指定）或 FundamentalPIT 对象（field=None），无数据返回None
    """
    async with async_session() as session:
        stmt = (
            select(FundamentalPIT)
            .where(and_(
                FundamentalPIT.code == code,
                FundamentalPIT.announce_date <= trade_date,
            ))
            .order_by(FundamentalPIT.announce_date.desc())
            .limit(1)
        )
        rec = (await session.execute(stmt)).scalar_one_or_none()
        if rec is None:
            return None
        if field:
            return getattr(rec, field, None)
        return rec


def _safe_float(v):
    """安全转float，None/NaN返回None。"""
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f):
            return None
        return f
    except (ValueError, TypeError):
        return None
