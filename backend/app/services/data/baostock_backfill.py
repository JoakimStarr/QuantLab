"""baostock 全量回填：从最新交易日向旧逐日拉全市场日K，重建 qlib bin + PG。

设计（按产品需求）：
- 数据源固定 baostock，一次拉全市场（query_daily_history_k_AStock），串行无并发（baostock 限制）
- 优先从最新数据向后同步（最新 → 最旧逐日拉取）
- 写两处：
  - qlib bin（open/high/low/close/volume/amount/change/tradable，喂因子引擎）
  - PG stock_daily（全部字段：含 preclose/turn/tradestatus/pct_chg/is_st/估值/adjustflag）
- 同时入库 stock_basic / stock_industry / trade_calendar
- 构建 instruments 文件（all/csi300/csi500/csiall）
- 增量由日期去重天然支持（ON CONFLICT DO NOTHING），重复执行只补缺失日期
"""
import asyncio
import logging
import os
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import settings
from app.core.database import async_session
from app.services.data.baostock_client import fetch_daily_all_a_stock_sync, from_baostock_code
from app.services.data.eod_incremental import _sync_stock_bin, _write_calendar, _compute_tradable
from app.services.data.sync_progress import (
    init_progress, update_progress, finish_progress, clear_progress,
)
from app.models.baostock import (
    StockDaily, StockBasic, StockIndustry, TradeCalendar,
)
from app.models.sync_history import SyncHistory
from app.models.stock_data_status import StockDataStatus

logger = logging.getLogger(__name__)

# qlib bin 写入字段（baostock 全部日线字段；is_st/tradestatus/adjustflag 存为 float 0/1 或数值）
BIN_FIELDS = [
    "open", "high", "low", "close", "preclose",
    "volume", "amount", "turn",
    "tradestatus", "pct_chg", "is_st",
    "pe_ttm", "pb_mrq", "ps_ttm", "pcf_ncf_ttm",
    "adjustflag",
    "change", "tradable",
]

# baostock 日线列名 -> stock_daily 列名
DAILY_COL_MAP = {
    "open": "open", "high": "high", "low": "low", "close": "close",
    "preclose": "preclose", "volume": "volume", "amount": "amount",
    "turn": "turn", "tradestatus": "tradestatus", "pctChg": "pct_chg",
    "isST": "is_st", "peTTM": "pe_ttm", "pbMRQ": "pb_mrq",
    "psTTM": "ps_ttm", "pcfNcfTTM": "pcf_ncf_ttm", "adjustflag": "adjustflag",
}

_CHUNK_DAYS = 50  # 每个批次拉取的交易日数（控制内存）


def _get_trade_dates(start: str, end: str) -> list:
    """同步获取 baostock 交易日历（仅交易日）。"""
    import baostock as bs
    rs = bs.query_trade_dates(start_date=start, end_date=end)
    dates = []
    while rs.error_code == "0" and rs.next():
        row = rs.get_row_data()  # [calendar_date, is_trading_day]
        if len(row) >= 2 and row[1] == "1":
            dates.append(row[0])
    return dates


def _numeric(df: pd.DataFrame, cols: list) -> None:
    """将列转为 float（baostock 返回字符串）。"""
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")


def _normalize_daily(df_all: pd.DataFrame) -> pd.DataFrame:
    """转换 baostock 全市场日K：代码转 qlib 格式 + 数值化 + isST 转 bool。"""
    df = df_all.copy()
    df["qlib_code"] = df["code"].apply(from_baostock_code)
    df["qlib_code_lower"] = df["qlib_code"].str.lower()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    _numeric(df, ["open", "high", "low", "close", "preclose", "volume", "amount",
                  "turn", "pctChg", "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM"])
    if "isST" in df.columns:
        df["isST"] = df["isST"].astype(str) == "1"
    if "tradestatus" in df.columns:
        df["tradestatus"] = pd.to_numeric(df["tradestatus"], errors="coerce")
    if "adjustflag" in df.columns:
        df["adjustflag"] = pd.to_numeric(df["adjustflag"], errors="coerce")
    return df


def _accumulate(per_stock: dict, df: pd.DataFrame) -> None:
    """把某日全市场数据按股票累积到 per_stock[code_lower] = list[dict]。"""
    for code_lower, grp in df.groupby("qlib_code_lower"):
        row = grp.iloc[0]
        per_stock.setdefault(code_lower, []).append({
            "date": row["date"],
            "open": row.get("open"), "high": row.get("high"), "low": row.get("low"),
            "close": row.get("close"), "preclose": row.get("preclose"),
            "volume": row.get("volume"), "amount": row.get("amount"),
            "turn": row.get("turn"), "tradestatus": row.get("tradestatus"),
            "pctChg": row.get("pctChg"), "isST": row.get("isST"),
            "peTTM": row.get("peTTM"), "pbMRQ": row.get("pbMRQ"),
            "psTTM": row.get("psTTM"), "pcfNcfTTM": row.get("pcfNcfTTM"),
            "adjustflag": row.get("adjustflag"),
        })


def _write_stock_bins(code_lower: str, rows: list, global_calendar: list,
                      qlib_dir: str) -> list:
    """写入单只股票的 qlib bin，并返回 stock_daily 记录。"""
    df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    if df.empty:
        return []
    qlib_code = code_lower.upper()

    out = pd.DataFrame({
        "date": df["date"],
        "open": df["open"].astype(float),
        "high": df["high"].astype(float),
        "low": df["low"].astype(float),
        "close": df["close"].astype(float),
        "preclose": df["preclose"].astype(float) if "preclose" in df.columns else np.nan,
        "volume": df["volume"].astype(float),
        "amount": df["amount"].astype(float),
        "turn": df["turn"].astype(float) if "turn" in df.columns else np.nan,
        "tradestatus": df["tradestatus"].astype(float) if "tradestatus" in df.columns else np.nan,
        "pct_chg": df["pctChg"].astype(float) if "pctChg" in df.columns else np.nan,
        "is_st": df["isST"].astype(float) if "isST" in df.columns else np.nan,
        "pe_ttm": df["peTTM"].astype(float) if "peTTM" in df.columns else np.nan,
        "pb_mrq": df["pbMRQ"].astype(float) if "pbMRQ" in df.columns else np.nan,
        "ps_ttm": df["psTTM"].astype(float) if "psTTM" in df.columns else np.nan,
        "pcf_ncf_ttm": df["pcfNcfTTM"].astype(float) if "pcfNcfTTM" in df.columns else np.nan,
        "adjustflag": df["adjustflag"].astype(float) if "adjustflag" in df.columns else np.nan,
    })
    # change = 涨跌幅(小数)；tradable 由涨跌幅+ST 判定
    out["change"] = (df["pctChg"].astype(float) / 100.0).fillna(0.0)
    is_st = df["isST"] if "isST" in df.columns and df["isST"].notna().any() else None
    out["tradable"] = _compute_tradable(
        out["close"], df["pctChg"].astype(float), code=qlib_code, is_st=is_st,
    )

    feat_dir = os.path.join(qlib_dir, "features", code_lower)
    _sync_stock_bin(feat_dir, out, global_calendar, BIN_FIELDS, overwrite=True)

    # stock_daily 全字段记录
    rec = []
    for _, r in df.iterrows():
        rec.append({
            "code": qlib_code,
            "trade_date": date.fromisoformat(r["date"]),
            "open": _f(r.get("open")), "high": _f(r.get("high")),
            "low": _f(r.get("low")), "close": _f(r.get("close")),
            "preclose": _f(r.get("preclose")), "volume": _f(r.get("volume")),
            "amount": _f(r.get("amount")), "turn": _f(r.get("turn")),
            "tradestatus": _i(r.get("tradestatus")), "pct_chg": _f(r.get("pctChg")),
            "is_st": bool(r.get("isST")) if pd.notna(r.get("isST")) else None,
            "pe_ttm": _f(r.get("peTTM")), "pb_mrq": _f(r.get("pbMRQ")),
            "ps_ttm": _f(r.get("psTTM")), "pcf_ncf_ttm": _f(r.get("pcfNcfTTM")),
            "adjustflag": _i(r.get("adjustflag")),
        })
    return rec


def _f(v):
    """float 转换，NaN/None -> None。"""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    try:
        f = float(v)
        return None if np.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _i(v):
    """int 转换，NaN/None -> None。"""
    f = _f(v)
    return int(f) if f is not None else None


def _flush_chunk(per_stock: dict, global_calendar: list, qlib_dir: str,
                 code_range: dict, pg_rows: list) -> int:
    """写一批股票 bin 并收集 stock_daily 记录，返回成功股票数。"""
    success = 0
    for code_lower, rows in per_stock.items():
        try:
            rec = _write_stock_bins(code_lower, rows, global_calendar, qlib_dir)
        except Exception as e:
            logger.debug("写 %s 失败: %s", code_lower, e)
            continue
        pg_rows.extend(rec)
        if rec:
            dates = sorted({r["trade_date"] for r in rec})
            prev = code_range.get(code_lower)
            code_range[code_lower] = [min(dates[0], prev[0]) if prev else dates[0],
                                      max(dates[-1], prev[1]) if prev else dates[-1]]
            success += 1
    return success


async def _insert_stock_daily(rows: list) -> None:
    if not rows:
        return
    # asyncpg 单条 SQL 最多 32767 个参数：18 字段 × 每行 = 18 参数，
    # 每批最多 1000 行（18000 参数），超出则拆批，避免 InterfaceError。
    BATCH_ROWS = 1000
    async with async_session() as session:
        for i in range(0, len(rows), BATCH_ROWS):
            chunk = rows[i:i + BATCH_ROWS]
            stmt = pg_insert(StockDaily.__table__).values(chunk)
            stmt = stmt.on_conflict_do_nothing(index_elements=["code", "trade_date"])
            await session.execute(stmt)
        await session.commit()


async def _insert_misc(df_basic, df_industry, trade_dates) -> None:
    """入库 stock_basic / stock_industry / trade_calendar（幂等，分批防 asyncpg 参数上限）。"""
    # asyncpg 单条 SQL 最多 32767 参数；全市场 ~5200 股一次插入会超限，
    # 分批每批 800 行（6 字段 × 800 = 4800 参数）。
    BATCH = 800

    def _chunk(rows: list):
        for i in range(0, len(rows), BATCH):
            yield rows[i:i + BATCH]

    async with async_session() as session:
        if df_basic is not None and not df_basic.empty:
            rows = []
            for _, r in df_basic.iterrows():
                rows.append({
                    "code": from_baostock_code(str(r["code"])),
                    "name": str(r["code_name"]) if pd.notna(r["code_name"]) else None,
                    "ipo_date": _parse_date(r.get("ipoDate")),
                    "out_date": _parse_date(r.get("outDate")),
                    "type": str(r["type"]) if pd.notna(r["type"]) else None,
                    "status": str(r["status"]) if pd.notna(r["status"]) else None,
                })
            for chunk in _chunk(rows):
                stmt = pg_insert(StockBasic.__table__).values(chunk)
                stmt = stmt.on_conflict_do_nothing(index_elements=["code"])
                await session.execute(stmt)

        if df_industry is not None and not df_industry.empty:
            rows = []
            for _, r in df_industry.iterrows():
                rows.append({
                    "code": from_baostock_code(str(r["code"])),
                    "code_name": str(r["code_name"]) if pd.notna(r["code_name"]) else None,
                    "industry": str(r["industry"]) if pd.notna(r["industry"]) else None,
                    "industry_classification": str(r["industryClassification"]) if pd.notna(r["industryClassification"]) else None,
                    "update_date": _parse_date(r.get("updateDate")),
                })
            for chunk in _chunk(rows):
                stmt = pg_insert(StockIndustry.__table__).values(chunk)
                stmt = stmt.on_conflict_do_nothing(index_elements=["code"])
                await session.execute(stmt)

        if trade_dates:
            rows = [{"trade_date": date.fromisoformat(d), "is_trading_day": True} for d in trade_dates]
            for chunk in _chunk(rows):
                stmt = pg_insert(TradeCalendar.__table__).values(chunk)
                stmt = stmt.on_conflict_do_nothing(index_elements=["trade_date"])
                await session.execute(stmt)
        await session.commit()


def _parse_date(v):
    if v is None or (isinstance(v, str) and not v.strip()) or pd.isna(v):
        return None
    try:
        return pd.to_datetime(str(v)).date()
    except (ValueError, TypeError):
        return None


def _fetch_all_sync(api_name: str, date_str: str) -> list:
    """同步执行 baostock 全市场查询（query_stock_basic/query_stock_industry/成分股）。"""
    import baostock as bs
    fn = getattr(bs, api_name)
    rs = fn(date=date_str) if api_name in ("query_hs300_stocks", "query_zz500_stocks") else fn()
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        return []
    fields = rs.fields
    return [dict(zip(fields, r)) for r in rows]


def _write_instrument_file(qlib_dir: str, name: str, entries: list) -> None:
    """写 instruments/{name}.txt：每行 code\\tstart\\tend（code 大写）。"""
    path = os.path.join(qlib_dir, "instruments", f"{name}.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for code, s, e in sorted(entries):
            f.write(f"{code}\t{s}\t{e}\n")


def _build_instruments(qlib_dir: str, code_range: dict, calendar: list,
                       hs300: list, zz500: list) -> None:
    """构建 instruments 文件（all/csiall/csi300/csi500）。"""
    cal_start, cal_end = calendar[0], calendar[-1]
    all_entries = [(c.upper(), s, e) for c, (s, e) in code_range.items()]
    _write_instrument_file(qlib_dir, "all", all_entries)
    _write_instrument_file(qlib_dir, "csiall", all_entries)
    if hs300:
        # hs300/zz500 来自 baostock query_hs300_stocks，code 为 baostock 格式（sh.600000），
        # 需转 qlib 格式（sh600000）再大写
        entries = [(from_baostock_code(c).upper(), cal_start, cal_end) for c in hs300]
        _write_instrument_file(qlib_dir, "csi300", entries)
    if zz500:
        entries = [(from_baostock_code(c).upper(), cal_start, cal_end) for c in zz500]
        _write_instrument_file(qlib_dir, "csi500", entries)


async def run_baostock_backfill(years: int, universe: str = "all") -> dict:
    """baostock 全量回填主入口（最新 → 最旧）。

    Args:
        years: 回填年数（0 表示仅增量补最新）
        universe: 股票池（all/csi300/csi500），用于状态记录
    """
    qlib_dir = settings.qlib_provider_path
    os.makedirs(os.path.join(qlib_dir, "calendars"), exist_ok=True)
    init_progress(universe, "baostock")

    try:
        await asyncio.to_thread(_ensure_login)
        end = datetime.now().date()
        start = end - timedelta(days=365 * years + 30) if years and years > 0 else end
        logger.info("baostock 回填: %s ~ %s (%d 年)", start, end, years or 0)

        update_progress(pct=2, status="running", message="获取 baostock 交易日历...")
        trade_dates = await asyncio.to_thread(
            _get_trade_dates, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
        )
        if not trade_dates:
            raise ValueError("baostock 交易日历为空，无法回填")
        global_calendar = sorted(trade_dates)
        desc_dates = list(reversed(global_calendar))
        logger.info("交易日数: %d", len(global_calendar))

        # 先写全局日历（_sync_stock_bin 依赖）
        _write_calendar(qlib_dir, global_calendar)

        per_stock = {}
        code_range = {}
        pg_rows = []
        total = len(desc_dates)
        success_stocks = 0

        for i, d in enumerate(desc_dates):
            try:
                # 单日拉取限时 120s：baostock 是同步阻塞 API 且无超时，
                # 一旦服务端连接挂起会永久卡住整个回填，超时则放弃该日继续。
                df_all = await asyncio.wait_for(
                    asyncio.to_thread(fetch_daily_all_a_stock_sync, d),
                    timeout=120,
                )
            except asyncio.TimeoutError:
                logger.warning("baostock 拉取 %s 超时(120s)，跳过该日", d)
                continue
            except Exception as e:
                logger.warning("baostock 拉取 %s 失败: %s", d, e)
                continue
            if df_all is None or df_all.empty:
                continue
            df_norm = _normalize_daily(df_all)
            _accumulate(per_stock, df_norm)

            if (i + 1) % _CHUNK_DAYS == 0 or (i + 1) == total:
                success_stocks += await asyncio.to_thread(
                    _flush_chunk, per_stock, global_calendar, qlib_dir, code_range, pg_rows
                )
                await _insert_stock_daily(pg_rows)
                logger.info("批次写入: %d/%d 日, 累计股票 %d, 当日记录 %d",
                            i + 1, total, success_stocks, len(pg_rows))
                pg_rows = []
                per_stock = {}

            update_progress(pct=5 + (i + 1) / total * 80,
                            status="running",
                            message=f"baostock 回填 {d} ({i + 1}/{total})")

        # 基础资料 / 行业 / 日历
        update_progress(pct=88, status="running", message="入库股票基本资料/行业分类...")
        df_basic = df_industry = None
        try:
            basic_rows = await asyncio.to_thread(_fetch_all_sync, "query_stock_basic", "")
            if basic_rows:
                df_basic = pd.DataFrame(basic_rows)
        except Exception as e:
            logger.warning("stock_basic 拉取失败: %s", e)
        try:
            ind_rows = await asyncio.to_thread(_fetch_all_sync, "query_stock_industry", "")
            if ind_rows:
                df_industry = pd.DataFrame(ind_rows)
        except Exception as e:
            logger.warning("stock_industry 拉取失败: %s", e)
        await _insert_misc(df_basic, df_industry, global_calendar)

        # 指数成分股 + instruments
        update_progress(pct=94, status="running", message="构建股票池 instruments...")
        hs300 = zz500 = []
        try:
            hs300 = [r["code"] for r in await asyncio.to_thread(
                _fetch_all_sync, "query_hs300_stocks", end.strftime("%Y-%m-%d"))]
        except Exception as e:
            logger.warning("hs300 成分拉取失败: %s", e)
        try:
            zz500 = [r["code"] for r in await asyncio.to_thread(
                _fetch_all_sync, "query_zz500_stocks", end.strftime("%Y-%m-%d"))]
        except Exception as e:
            logger.warning("zz500 成分拉取失败: %s", e)
        _build_instruments(qlib_dir, code_range, global_calendar, hs300, zz500)

        # 更新同步状态
        await _update_sync_status(universe, qlib_dir, global_calendar, code_range)
        finish_progress(True)
        clear_progress()

        result = {
            "ok": True, "source": "baostock", "universe": universe,
            "years": years, "trade_days": len(global_calendar),
            "stocks": len(code_range),
            "calendar_start": global_calendar[0], "calendar_end": global_calendar[-1],
        }
        logger.info("baostock 回填完成: %s", result)
        return result

    except Exception as e:
        finish_progress(False, str(e))
        clear_progress()
        logger.exception("baostock 回填失败")
        raise


async def _update_sync_status(universe: str, qlib_dir: str, calendar: list,
                              code_range: dict) -> None:
    """更新 stock_data_status 并写 sync_history。"""
    from sqlalchemy import select, func
    now = datetime.now()
    async with async_session() as session:
        row_cnt = (await session.execute(select(func.count()).select_from(StockDaily))).scalar() or 0
        existing = await session.execute(
            select(StockDataStatus).where(StockDataStatus.universe == universe)
        )
        rec = existing.scalar_one_or_none()
        if rec is None:
            rec = StockDataStatus(universe=universe)
            session.add(rec)
        rec.latest_date = calendar[-1]
        rec.stock_count = len(code_range)
        rec.row_count = row_cnt
        rec.status = "ok"
        rec.last_error = None
        rec.qlib_dir = qlib_dir
        rec.last_updated = now
        await session.commit()

        h = SyncHistory(
            universe=universe, data_source="baostock", sync_path="baostock_backfill",
            status="ok", started_at=now, finished_at=now,
            latest_date=calendar[-1], stock_count=len(code_range),
            row_count=row_cnt,
        )
        session.add(h)
        await session.commit()


def classify_sync_error(error: str) -> dict:
    """根据错误信息分类失败原因，返回分类标签与建议解决方案。"""
    err = (error or "").lower()
    if any(k in err for k in (
        "connection aborted", "remotedisconnected", "connectionerror",
        "timeout", "timed out", "ssl", "urlopen", "max retries", "network",
        "proxy", "connection reset",
    )):
        return {
            "category": "network", "category_label": "网络错误",
            "suggestion": "请检查网络连接后重试。baostock 连接不稳定时建议稍后重试。",
        }
    if any(k in err for k in ("no space left", "enospc", "disk", "磁盘空间不足", "quota")):
        return {
            "category": "disk_full", "category_label": "磁盘空间不足",
            "suggestion": "磁盘空间不足，请清理临时文件或扩容后重试。",
        }
    if any(k in err for k in ("corrupt", "checksum", "readerror", "解压", "day.txt", "数据可能不完整")):
        return {
            "category": "data_corrupt", "category_label": "数据损坏",
            "suggestion": "本地数据可能损坏，建议删除后重新回填。",
        }
    if any(k in err for k in ("container restart", "interrupted", "sync timeout", "同步超时")):
        return {
            "category": "interrupted", "category_label": "同步被中断",
            "suggestion": "同步过程被中断，建议重试同步（可开启自动重试：config.quant.auto_retry_sync=true）。",
        }
    if any(k in err for k in ("login", "认证", "auth")):
        return {
            "category": "auth_failed", "category_label": "认证失败",
            "suggestion": "baostock 登录失败，请检查网络后重试。",
        }
    return {"category": "unknown", "category_label": "未知错误", "suggestion": "请查看后端日志排查，或重试同步。"}


async def mark_sync_failed(universe: str, error: str):
    """标记同步失败，并在 last_error 中附上失败分类与建议解决方案。"""
    cls = classify_sync_error(error)
    friendly = "[{label}] {err}\n建议: {sug}".format(
        label=cls["category_label"], err=error[:400], sug=cls["suggestion"]
    )
    logger.error("数据同步失败 universe=%s category=%s: %s", universe, cls["category"], error[:500])
    async with async_session() as session:
        existing = await session.execute(
            select(StockDataStatus).where(StockDataStatus.universe == universe)
        )
        rec = existing.scalar_one_or_none()
        if rec is None:
            rec = StockDataStatus(universe=universe)
            session.add(rec)
        rec.status = "failed"
        rec.last_error = friendly[:500]
        rec.last_updated = datetime.now()
        await session.commit()


async def run_baostock_backfill_task(req) -> None:
    """后台任务包装：按请求执行 baostock 回填并更新状态。"""
    from app.schemas.quant import SyncDataRequest
    universe = req.universe or settings.quant.get("universe", "csi300")
    years = req.years or int(settings.quant.get("backfill_years", 5))
    try:
        result = await run_baostock_backfill(years=years, universe=universe)
        logger.info("baostock 回填后台任务完成: %s", result)
    except Exception as e:
        await mark_sync_failed(universe, str(e))
        logger.exception("baostock 回填后台任务失败")


def _ensure_login():
    from app.services.data.baostock_client import _ensure_login as _login
    _login()
