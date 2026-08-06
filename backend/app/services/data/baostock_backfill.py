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
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import settings
from app.core.database import async_session
from app.services.data.baostock_client import (
    BaostockQuotaError,
    fetch_daily_all_a_stock_sync,
    from_baostock_code,
)
from app.services.data.data_clean import format_date_series, to_float_strict as _f
from app.services.data.db_utils import bulk_upsert
from app.services.data.eod_incremental import _sync_stock_bin, _write_calendar, _compute_tradable, _get_calendar
from app.services.data.sync_progress import (
    init_progress, update_progress, finish_progress, clear_progress,
)
from app.models.baostock import (
    StockDaily, StockBasic, StockIndustry, TradeCalendar,
)
from app.models.sync_history import SyncHistory
from app.models.stock_data_status import StockDataStatus

logger = logging.getLogger(__name__)

# 字段清单与 baostock 列映射收敛到 data_fields.py（见 STOCK_BIN_FIELDS / BAOSTOCK_DAILY_COL_MAP）
from app.services.data.data_fields import STOCK_BIN_FIELDS as BIN_FIELDS

# 每个批次拉取的交易日数（控制内存）：1 = 每下载一天即写入，
# 数据实时落盘、崩溃丢失少；调大可减少写盘次数但内存占用更高。
# 默认 20：整市场 5200 只股票的 bin 全量重写一次约 2-4 分钟，chunk_days=1 时
# 消费者跟不上生产者，生产者入队必超时；20 把写盘次数降到 ~1/20，全程可完成。
_CHUNK_DAYS = int(os.environ.get("QUANTLAB_BACKFILL_CHUNK_DAYS", "20"))

# 写入侧并行度：_flush_chunk 同时并写多少只股票的 bin（相互独立，可并行）
_WRITE_WORKERS = int(os.environ.get("QUANTLAB_BACKFILL_WRITE_WORKERS", "16"))
# 下载与写入流水线的缓冲队列上限（只缓存当日拉取结果，避免内存膨胀）
_QUEUE_MAX = int(os.environ.get("QUANTLAB_BACKFILL_QUEUE", "8"))

_write_pool = None  # 全局共享的 bin 并行写入线程池（见 _get_write_pool）


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
    df["date"] = format_date_series(df["date"])
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


def _build_out_df(code_lower: str, df: pd.DataFrame) -> pd.DataFrame:
    """把 baostock 格式行（date/open/.../pctChg/isST/...）转成 qlib bin 写入帧。

    out 包含 BIN_FIELDS 全部字段：stock_daily 16 个数据列 + 衍生字段
    change(=pctChg/100) 和 tradable(涨跌停判定)。被回填与 PG 重建共用。
    """
    df = df.sort_values("date").reset_index(drop=True)
    if df.empty:
        return df
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
        # 价格已按 adjustflag=qfq 前复权存储，factor 统一为 1.0：
        # qlib 依赖 $factor 判断价格是否复权（factor 全 NaN 会进入 adjusted_price 模式，
        # 导致 trade_unit=100 的整手取整失效，成交出现碎股）。
        "factor": 1.0,
    })
    # change = 涨跌幅(小数)；tradable 由涨跌幅+ST 判定
    out["change"] = (df["pctChg"].astype(float) / 100.0).fillna(0.0)
    is_st = df["isST"] if "isST" in df.columns and df["isST"].notna().any() else None
    out["tradable"] = _compute_tradable(
        out["close"], df["pctChg"].astype(float), code=qlib_code, is_st=is_st,
    )
    return out


def _write_stock_bins(code_lower: str, rows: list, global_calendar: list,
                      qlib_dir: str, old_calendar: list = None) -> list:
    """写入单只股票的 qlib bin，并返回 stock_daily 记录。"""
    df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    if df.empty:
        return []
    qlib_code = code_lower.upper()

    out = _build_out_df(code_lower, df)

    feat_dir = os.path.join(qlib_dir, "features", code_lower)
    _sync_stock_bin(feat_dir, out, global_calendar, BIN_FIELDS, overwrite=True,
                    old_calendar=old_calendar)

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


def _i(v):
    """int 转换，NaN/None -> None。"""
    f = _f(v)
    return int(f) if f is not None else None


def _get_write_pool():
    """全局共享的 bin 并行写入线程池（进程生命周期，避免每个批次重复创建）。"""
    global _write_pool
    if _write_pool is None:
        from concurrent.futures import ThreadPoolExecutor
        _write_pool = ThreadPoolExecutor(max_workers=_WRITE_WORKERS, thread_name_prefix="qlib-bin-write")
    return _write_pool


def _flush_chunk(per_stock: dict, global_calendar: list, qlib_dir: str,
                 code_range: dict, pg_rows: list, old_calendar: list = None,
                 written_codes: set = None) -> int:
    """写一批股票 bin 并收集 stock_daily 记录，返回成功股票数。

    每只股票的 bin 写入相互独立，用线程池并行写，缩短写盘耗时，
    避免写盘拖慢整体下载节奏。

    old_calendar: 回填前 day.txt（本次运行前 bin 对齐的日历）。
    written_codes: 本次回填中已重写过 bin 的股票集合（由调用方维护）。
        关键：同一股票在多个批次被写入时，第一批之后其 bin 已按 global_calendar
        对齐，后续批次必须以 global_calendar 作 old_calendar 去映射旧值；若仍传
        回填前的 old_calendar，前几批写入的新日期（位于旧日历长度之后）会被映射
        为 -1 而静默丢弃（数据丢失 bug）。
    """
    ex = _get_write_pool()
    success = 0
    futures = {}
    for code_lower, rows in per_stock.items():
        ref_cal = global_calendar if (written_codes and code_lower in written_codes) else old_calendar
        futures[code_lower] = ex.submit(
            _write_stock_bins, code_lower, rows, global_calendar, qlib_dir, ref_cal
        )
    for code_lower, fut in futures.items():
        try:
            rec = fut.result()
        except Exception as e:
            logger.debug("写 %s 失败: %s", code_lower, e)
            continue
        pg_rows.extend(rec)
        if rec:
            # 写入成功才标记"已按 global_calendar 对齐"；失败/无记录的股票保持
            # 回填前对齐，后续批次仍用 old_calendar 映射旧值
            if written_codes is not None:
                written_codes.add(code_lower)
            # code_range 种子来自 _load_existing_ranges（字符串 'YYYY-MM-DD'），
            # 新记录是 datetime.date —— 统一转字符串再比较，避免 min/max 跨类型崩溃
            dates = sorted({_fmt_ymd(r["trade_date"]) for r in rec})
            prev = code_range.get(code_lower)
            code_range[code_lower] = [min(dates[0], prev[0]) if prev else dates[0],
                                      max(dates[-1], prev[1]) if prev else dates[-1]]
            success += 1
    return success


def _fmt_ymd(v):
    """任意日期值 → 'YYYY-MM-DD' 字符串。"""
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    return str(v)[:10]


def _as_date(v):
    """任意日期值 → datetime.date（asyncpg DATE 列需要 date 对象而非 str）。"""
    if isinstance(v, str):
        return date.fromisoformat(v[:10])
    if isinstance(v, datetime):
        return v.date()
    return v


async def _insert_stock_daily(rows: list) -> set:
    """批量落库 stock_daily（幂等，分批防 asyncpg 参数上限）。

    Returns:
        本批实际持久化的交易日集合（YYYY-MM-DD），仅当 commit 成功才返回，
        调用方据此把新日期回填到 day.txt，保证日历与数据库同步。
    """
    if not rows:
        return set()
    # 防御：asyncpg 对 DATE 列要求 date 对象，传 str 会抛 DataError；
    # 统一转成 date 对象，避免个别记录类型漂移导致整批插入失败
    rows = [dict(r, trade_date=_as_date(r["trade_date"])) for r in rows]
    dates = {_fmt_ymd(r["trade_date"]) for r in rows}
    # asyncpg 单条 SQL 最多 32767 参数：约 19 字段 × 每行，每批 1000 行足够安全
    await bulk_upsert(StockDaily, rows, ["code", "trade_date"], batch=1000)
    return dates


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


def _fetch_membership_history(api_name: str, sample_dates: list) -> dict:
    """拉取指数点按时点成分，返回 {code: [(start, end), ...]} 成员区间。

    背景：baostock query_hs300_stocks(date) 返回"该日期"的指数成分（点按时点），
    支持按历史日期查询。定期采样后拼接成每只股票的成员区间，写入 instruments
    文件可实现动态成分股（消除幸存者偏差：只有当时在指数内的股票才可交易）。
    """
    spans_by_code: dict[str, list] = {}
    prev_dates: dict[str, str] = {}
    from app.services.data.baostock_client import _ensure_login
    _ensure_login()
    for sample in sample_dates:
        try:
            rows = _fetch_all_sync(api_name, sample)
        except Exception as e:  # noqa: BLE001
            logger.warning("%s 采样 %s 失败: %s", api_name, sample, e)
            continue
        cur_codes = {from_baostock_code(r["code"]).lower() for r in rows}
        # 上期在册且本期不在册 -> 关闭区间
        for code in list(prev_dates.keys()):
            if code not in cur_codes:
                spans_by_code.setdefault(code, []).append((prev_dates.pop(code), sample))
        # 本期在册 -> 记录起点（若已 open 则保持）
        for code in cur_codes:
            prev_dates.setdefault(code, sample)
    # 尾随区间：仍在册的延伸到采样最后一天
    last_sample = sample_dates[-1]
    for code, start in prev_dates.items():
        spans_by_code.setdefault(code, []).append((start, last_sample))
    # 相邻区间合并（防御：同一代码多次连续在册被重复追加）
    for code, spans in spans_by_code.items():
        spans.sort()
        merged = [spans[0]]
        for s, e in spans[1:]:
            if s <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        spans_by_code[code] = merged
    return spans_by_code


def _rebuild_dynamic_instruments(qlib_dir: str, calendar: list,
                                 sample_interval_days: int = 90) -> dict:
    """按点按时点重建 csi300/csi500 动态成分 instruments 文件。

    用 baostock 历史成分查询，以 sample_interval_days 间隔采样整个日历，
    生成每只股票的真实成员区间（可多次写入同一 code 不同日期段）。
    qlib 的 instruments 解析会按 (start, end) 区间掩码因子数据，
    从而在回测/评价中实现动态成分（避免幸存者偏差）。

    Returns:
        dict: 各指数文件写入的代码数（供日志/状态展示）。
    """
    cal_start, cal_end = calendar[0], calendar[-1]
    if len(calendar) < 2:
        return {}
    sample_dates = calendar[::max(1, sample_interval_days)]
    if sample_dates[-1] != cal_end:
        sample_dates.append(cal_end)

    counts = {}
    for name, api in (("csi300", "query_hs300_stocks"), ("csi500", "query_zz500_stocks")):
        try:
            spans = _fetch_membership_history(api, sample_dates)
        except Exception as e:  # noqa: BLE001
            logger.warning("%s 动态成分重建失败: %s", name, e)
            continue
        entries = []
        for code, spans_list in spans.items():
            for s, e in spans_list:
                entries.append((code, s, e))
        _write_instrument_file(qlib_dir, name, entries)
        counts[name] = len(spans)
        logger.info("%s 动态成分写入完成: %d 只股票", name, len(spans))
    return counts


def _build_instruments(qlib_dir: str, code_range: dict, calendar: list,
                       hs300: list, zz500: list) -> None:
    """构建 instruments 文件（all/csiall/csi300/csi500）。

    注意：qlib 的 instruments 代码必须小写（与 features/ 目录名一致，如 sh600000），
    大写会导致 qlib D.instruments 返回空、因子评估/校验全部失败。
    """
    cal_start, cal_end = calendar[0], calendar[-1]
    all_entries = [(c.lower(), s, e) for c, (s, e) in code_range.items()]
    _write_instrument_file(qlib_dir, "all", all_entries)
    _write_instrument_file(qlib_dir, "csiall", all_entries)
    if hs300:
        # hs300/zz500 来自 baostock query_hs300_stocks，code 为 baostock 格式（sh.600000），
        # 需转 qlib 格式（sh600000），与 features 目录一致保持小写
        entries = [(from_baostock_code(c).lower(), cal_start, cal_end) for c in hs300]
        _write_instrument_file(qlib_dir, "csi300", entries)
    if zz500:
        entries = [(from_baostock_code(c).lower(), cal_start, cal_end) for c in zz500]
        _write_instrument_file(qlib_dir, "csi500", entries)


def _select_new_dates(trade_dates: list, already_downloaded: set) -> list:
    """返回本次仍需下载的交易日（从最新到最旧），跳过已落库的日期。

    Args:
        trade_dates: 本次回填窗口内的交易日（升序）
        already_downloaded: 已落库 stock_daily 的日期集合（YYYY-MM-DD）

    Returns:
        list: 尚未下载的日期，按最新 → 最旧排序。
    """
    return [d for d in reversed(trade_dates) if d not in already_downloaded]


async def _load_existing_dates() -> set:
    """读取已落库 stock_daily 的交易日集合（YYYY-MM-DD）。

    用于去重补充信号：增量EOD等路径只写 qlib bin + 日历、不写 stock_daily，
    因此不能单独作为判断依据，仅与日历取并集。
    """
    async with async_session() as session:
        result = await session.execute(select(StockDaily.trade_date).distinct())
        return {row[0].strftime("%Y-%m-%d") for row in result}


async def rebuild_calendar_from_db(qlib_dir: str = None) -> list:
    """以数据库 stock_daily 的交易日为准，全量重建 qlib 日历 day.txt。

    day.txt 与已落库日期保持完全一致（数据库是权威），返回写回的日期列表（升序）。
    在回填结束时调用，保证任何路径写入的数据都被数据库如实反映到日历。
    """
    qlib_dir = qlib_dir or settings.qlib_provider_path
    dates = sorted(await _load_existing_dates())
    _write_calendar(qlib_dir, dates)
    logger.info("重建日历 day.txt: %d 个交易日（来自 stock_daily）", len(dates))
    return dates


def _load_feature_ranges(qlib_dir: str, calendar: list) -> dict:
    """从 features 目录推断每股数据区间（stock_daily 为空时的回退）。

    qlib bin = 4 字节头 + n×4 字节 float32，start_index 恒为 0，
    因此数据点数 n 对应日历 [calendar[0], calendar[n-1]]，即该股已有数据范围。
    覆盖增量EOD等只写 bin、不写 stock_daily 的历史数据。
    """
    feat_root = os.path.join(qlib_dir, "features")
    if not calendar or not os.path.isdir(feat_root):
        return {}
    ranges = {}
    for name in os.listdir(feat_root):
        if not os.path.isdir(os.path.join(feat_root, name)):
            continue
        bin_path = os.path.join(feat_root, name, "close.day.bin")
        if not os.path.exists(bin_path):
            continue
        n = (os.path.getsize(bin_path) - 4) // 4
        if n <= 0:
            continue
        end = calendar[min(n - 1, len(calendar) - 1)]
        ranges[name] = [calendar[0], end]
    return ranges


async def _load_existing_ranges(qlib_dir: str, calendar: list) -> dict:
    """读取每只股票已有的数据区间 {code_lower: [最早日期, 最晚日期]}。

    跳过已下载日期后只处理新增日期，但 instruments 文件仍需保留每只股票
    真实的最早/最晚数据，因此以已有的区间作种子，再与新下载的日期合并
    （取并集最早/最晚）。优先用 stock_daily 精确区间，表为空时回退到
    features 目录按 bin 长度推断。
    """
    async with async_session() as session:
        result = await session.execute(
            select(
                StockDaily.code,
                func.min(StockDaily.trade_date),
                func.max(StockDaily.trade_date),
            ).group_by(StockDaily.code)
        )
        ranges = {
            code.lower(): [min_d.strftime("%Y-%m-%d"), max_d.strftime("%Y-%m-%d")]
            for code, min_d, max_d in result
        }
    if ranges:
        return ranges
    return _load_feature_ranges(qlib_dir, calendar)


async def _run_backfill_downloads(
    to_download: list,
    global_calendar: list,
    qlib_dir: str,
    code_range: dict,
    chunk_days: int = 1,
    queue_max: int = 8,
    written_days: set = None,
    old_calendar: list = None,
) -> int:
    """流水线式回填下载：串行拉取 + 后台并行写盘，写盘不耽误下载。

    生产者串行拉取每日全市场数据（baostock 禁止并发连接，只能串行）；
    消费者在独立任务中把拉取结果累加并写 qlib bin / 落库 stock_daily，
    与后续日期的下载并行执行。_flush_chunk 内部再按股票多线程并写。
    每批数据读写成功后，立即把该批交易日回填到 day.txt，保持日历与数据库同步。

    Args:
        written_days: 已下载交易日种子集合（初始化 day.txt 已有内容），
            每批落库成功后并入新日期并重写 day.txt。

    Returns:
        成功写入的股票数（跨所有日期累计）。
    """
    from app.services.data.sync_progress import update_progress as _up
    queue = asyncio.Queue(maxsize=max(queue_max, 1))
    total = len(to_download)
    success_stocks = 0
    # 本次回填已重写过 bin 的股票集合：其 bin 已按 global_calendar 对齐，
    # 后续批次必须以 global_calendar 映射旧值（否则前几批数据被丢弃）
    written_codes = set()
    if written_days is None:
        written_days = set(_get_calendar(qlib_dir))

    async def _producer():
        for i, d in enumerate(to_download):
            try:
                # 单日拉取限时 120s：baostock 是同步阻塞 API 且无超时，
                # 一旦服务端连接挂起会永久卡住整个回填，超时则放弃该日继续。
                df_all = await asyncio.wait_for(
                    asyncio.to_thread(fetch_daily_all_a_stock_sync, d),
                    timeout=120,
                )
                if df_all is None or df_all.empty:
                    continue
                df_norm = _normalize_daily(df_all)
            except asyncio.TimeoutError:
                logger.warning("baostock 拉取 %s 超时(120s)，跳过该日", d)
                continue
            except BaostockQuotaError as e:
                # 当日请求配额耗尽，中止整个回填，避免逐日无谓重试
                logger.error("baostock 回填中止: %s", e)
                break
            except Exception as e:
                logger.warning("baostock 拉取 %s 失败: %s", d, e)
                continue
            _up(pct=5 + (i + 1) / total * 80, status="running",
                message=f"baostock 回填 {d} ({i + 1}/{total})")
            # fetch 成功后才检查消费者健康：消费者已退出则立即中止，
            # 否则数据只会在队列里越积越多，且 queue.put 无超时会永久挂起
            if consumer_task.done():
                raise RuntimeError("backfill 消费者任务已退出，同步中止")
            # 入队超时 600s：消费者每批要重写全市场 bin（分钟级），
            # 短超时会误杀健康同步；600s 只兜底真正卡死的场景
            await asyncio.wait_for(queue.put((d, df_norm)), timeout=600)

    async def _consumer():
        nonlocal success_stocks
        per_stock = {}
        pg_rows = []
        processed = 0
        while True:
            item = await queue.get()
            if item is None:
                if per_stock:
                    success_stocks += await asyncio.wait_for(
                        asyncio.to_thread(
                            _flush_chunk, per_stock, global_calendar, qlib_dir, code_range, pg_rows, old_calendar, written_codes
                        ),
                        timeout=600,
                    )
                    _dates = await asyncio.wait_for(_insert_stock_daily(pg_rows), timeout=300)
                    if _dates:
                        written_days.update(_dates)
                        _write_calendar(qlib_dir, sorted(written_days))
                    logger.info("尾批写入: 累计 %d/%d 日, 股票 %d, 当日记录 %d, 日历 %d",
                                processed, total, success_stocks, len(pg_rows), len(written_days))
                break
            _d, df_norm = item
            _accumulate(per_stock, df_norm)
            processed += 1
            if processed % chunk_days == 0:
                success_stocks += await asyncio.wait_for(
                    asyncio.to_thread(
                        _flush_chunk, per_stock, global_calendar, qlib_dir, code_range, pg_rows, old_calendar, written_codes
                    ),
                    timeout=600,
                )
                _dates = await asyncio.wait_for(_insert_stock_daily(pg_rows), timeout=300)
                if _dates:
                    # 数据读写成功即回填日历，与数据库保持同步
                    written_days.update(_dates)
                    _write_calendar(qlib_dir, sorted(written_days))
                logger.info("批次写入: %d/%d 日, 累计股票 %d, 当日记录 %d, 日历 %d",
                            processed, total, success_stocks, len(pg_rows), len(written_days))
                pg_rows = []
                per_stock = {}

    producer_task = asyncio.create_task(_producer())
    consumer_task = asyncio.create_task(_consumer())

    try:
        # 等生产者结束（内部检测到消费者死亡会主动抛错，不会永久挂起）
        await producer_task
        # 生产者正常结束：放哨兵让消费者写尾批
        await asyncio.wait_for(queue.put(None), timeout=600)
        await consumer_task
    finally:
        # 消费者异常必须记录，否则会被静默吞掉
        if consumer_task.done() and not consumer_task.cancelled():
            exc = consumer_task.exception()
            if exc is not None:
                logger.error("backfill 消费者异常: %s", exc)
        if not consumer_task.done():
            consumer_task.cancel()
        if not producer_task.done():
            producer_task.cancel()
    return success_stocks


async def run_baostock_backfill(years: int, universe: str = "all", kind: str = "backfill") -> dict:
    """baostock 全量回填主入口（最新 → 最旧）。

    增量去重：是否已下载以数据库 stock_daily 为准（day.txt 由库重建、与之对齐），
    已下载的数据不再重复拉取，重复执行只补缺失日期。下载与写盘走
    生产者-消费者流水线：网络拉取串行（baostock 禁止并发连接），
    写 bin + 落库在后台并行执行，互不耽误。每批数据读写成功后立即回填
    day.txt，与数据库保持实时同步。

    Args:
        years: 回填年数（0 表示仅增量补最新）
        universe: 股票池（all/csi300/csi500），用于状态记录
        kind: 任务归属（backfill worker 默认 "backfill"；repair 补齐步骤传 "repair"，
            避免进度标识被覆盖成 backfill）
    """
    qlib_dir = settings.qlib_provider_path
    os.makedirs(os.path.join(qlib_dir, "calendars"), exist_ok=True)
    init_progress(universe, "baostock", writes_bins=True, kind=kind)

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
        # 全局日历 = 本次回填交易日 ∪ 已有日历（bin 必须对齐完整日历，
        # 否则旧 bin 的 start_index+len 超出本次回填范围，触发对齐校验被丢弃）
        existing_calendar = _get_calendar(qlib_dir)
        global_calendar = sorted(set(trade_dates) | set(existing_calendar))
        # 去重：是否已下载以数据库 stock_daily 为准（决策口径）。
        # 注意：不要并入 existing_calendar（day.txt）——若上次同步把尚无数据的
        # 交易日（如"今天"）写进了 day.txt，并进去会导致该日永远不再补拉。
        already_downloaded = await _load_existing_dates()
        to_download = _select_new_dates(trade_dates, already_downloaded)
        # 用既有每股数据区间作种子，保证跳过已下载日期后 instruments 仍保留最早历史；
        # stock_daily 为空（如历史数据来自增量EOD路径）时回退到 features 目录推断。
        code_range = await _load_existing_ranges(qlib_dir, global_calendar)
        logger.info("交易日数: 本次 %d, 已下载 %d, 需下载 %d, 合并日历 %d",
                    len(trade_dates), len(already_downloaded),
                    len(to_download), len(global_calendar))

        if to_download:
            # 流水线：下载串行（baostock 禁止并发连接），写入在后台消费者中并行执行，
            # 写盘不耽误下载；_flush_chunk 内部再按股票多线程并写。
            success_stocks = await _run_backfill_downloads(
                to_download, global_calendar, qlib_dir, code_range,
                chunk_days=_CHUNK_DAYS, queue_max=_QUEUE_MAX,
                written_days=set(already_downloaded),
                # 旧 bin 对齐的是回填前的 day.txt（位于 global_calendar 后缀），
                # 必须传旧日历，否则旧数据会被映射到错误位置而丢失
                old_calendar=existing_calendar,
            )
        else:
            success_stocks = 0
            logger.info("无需下载新日期，跳过逐日拉取")
            update_progress(pct=85, status="running",
                            message=f"数据已是最新（{len(already_downloaded)} 个交易日），跳过下载")

        # 数据落盘后按数据库重建日历，保证 day.txt 与已落库日期完全对齐；
        # 中断时也只写到库里已有的日期，不会污染日历导致后续漏下。
        await rebuild_calendar_from_db(qlib_dir)
        # bin 是按 global_calendar（含窗口内尚无数据的交易日，如"今天"）对齐写入的，
        # day.txt 必须并入 global_calendar 保持一致，否则 bin 比 day.txt 多一天，
        # 所有股票 bin 会被校验标记为长度异常。无数据的日子是全 NaN，收盘后增量同步会补上。
        _data_dates = set(await _load_existing_dates())
        _final_cal = sorted(_data_dates | set(global_calendar))
        if len(_final_cal) != len(_data_dates):
            _write_calendar(qlib_dir, _final_cal)
            logger.info("day.txt 并入窗口交易日: %d -> %d 天", len(_data_dates), len(_final_cal))

        # 日历扩展后，无新日数据的股票（退市/长期停牌）bin 仍是旧长度，统一补 NaN 对齐，
        # 否则校验会报"长度异常"（如新增"今天"一个交易日时）
        from app.services.data.eod_incremental import _pad_bins_to_calendar
        _pad_bins_to_calendar(qlib_dir, _final_cal)

        # 日历可能被本轮回填扩展（如 5 年→10 年）；若此前已广播过外盘因子
        # （对齐到旧日历），这里按最终日历重新对齐广播，避免长度异常。
        try:
            from app.services.data.external_market import rebroadcast_external_market
            rb = await asyncio.to_thread(rebroadcast_external_market, qlib_dir)
            if rb.get("rebroadcasted"):
                logger.info("外盘因子已按最终日历重新对齐广播: %s", rb)
        except Exception as e:  # noqa: BLE001
            logger.warning("外盘因子重新对齐广播失败（可稍后手动重拉）: %s", e)
        # 宏观字段同样按最终日历重广播（尽力而为，失败可稍后在宏观页同步）
        try:
            from app.services.data.macro_sync import broadcast_macro_to_bins
            n = await broadcast_macro_to_bins(qlib_dir)
            if n:
                logger.info("宏观字段已按最终日历重广播: %d 股票字段", n)
        except Exception as e:  # noqa: BLE001
            logger.warning("宏观字段重广播失败（可稍后在宏观页同步）: %s", e)

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

        # 指数成分股 + instruments（动态成分：按点按时点采样，消除幸存者偏差）
        update_progress(pct=94, status="running", message="构建动态股票池 instruments...")
        try:
            _rebuild_dynamic_instruments(qlib_dir, global_calendar)
        except Exception as e:  # noqa: BLE001
            logger.warning("动态成分重建失败，回退静态快照: %s", e)
            hs300 = zz500 = []
            try:
                hs300 = [r["code"] for r in await asyncio.to_thread(
                    _fetch_all_sync, "query_hs300_stocks", end.strftime("%Y-%m-%d"))]
            except Exception as e2:  # noqa: BLE001
                logger.warning("hs300 成分拉取失败: %s", e2)
            try:
                zz500 = [r["code"] for r in await asyncio.to_thread(
                    _fetch_all_sync, "query_zz500_stocks", end.strftime("%Y-%m-%d"))]
            except Exception as e2:  # noqa: BLE001
                logger.warning("zz500 成分拉取失败: %s", e2)
            _build_instruments(qlib_dir, code_range, global_calendar, hs300, zz500)

        # 更新同步状态
        await _update_sync_status(universe, qlib_dir, global_calendar, code_range)
        finish_progress(True)
        # 延迟清除进度：给前端进度轮询留出读取 done 状态的窗口，否则立即为 None
        await asyncio.sleep(3)
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
        # 延迟清除进度，给前端进度轮询留出读取 failed 状态的窗口
        await asyncio.sleep(3)
        clear_progress()
        logger.exception("baostock 回填失败")
        raise


async def _update_sync_status(universe: str, qlib_dir: str, calendar: list,
                              code_range: dict, sync_path: str = "baostock_backfill") -> None:
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
            universe=universe, data_source="baostock", sync_path=sync_path,
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
    if any(k in err for k in ("黑名单", "blacklist", "限流", "rate limit", "访问频率",
                              "请求过于频繁", "10001011", "频率限制", "频繁访问")):
        return {
            "category": "rate_limited", "category_label": "接口限流",
            "suggestion": "数据源接口限流/黑名单（10001011），请间隔 10 分钟以上再试，或降低同步频率；纯 PG 重建的 repair 不受影响。",
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
    # 回填本质是全市场拉取，universe 仅作状态记录标签；默认 all 反映真实范围
    universe = req.universe or "all"
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
