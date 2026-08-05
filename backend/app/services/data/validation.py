"""增强数据完整性校验：全市场 bin 结构 + 数据库/qlib 字段与覆盖一致性。

与 integrity_check.check_integrity（qlib 可读性冒烟检查）不同，本模块做
跨存储比对：
  - fields:   全市场每只股票 × 18 个 bin 字段的文件完整性与长度
  - fieldset: stock_daily 数据列 ⊆ bin 字段（change/tradable 为预期衍生）
  - calendar: day.txt vs stock_daily 交易日 vs trade_calendar
  - coverage: 每股数据区间（数据库 GROUP BY min/max vs bin 长度）
  - qlib:     复用 check_integrity 的抽样加载

返回结构化报告，drift 段供前端决定是否展示"一键补齐"。
"""
import logging
import os

from sqlalchemy import select, func

from app.core.config import settings
from app.core.database import async_session
from app.core.executor import run_io_cpu
from app.models.baostock import StockDaily, TradeCalendar
from app.models.stock_data_status import StockDataStatus
# bin 格式常量（4 字节 float32 start_index 头）单一来源，避免多份定义漂移
from app.services.data.eod_incremental import QLIB_BIN_HEADER_SIZE

logger = logging.getLogger(__name__)

# 样本列表最大条数（避免响应过大）
MAX_SAMPLES = 20


def _clip(items, n=MAX_SAMPLES):
    """截断样本列表。"""
    return items[:n]


def _status_from_counts(error=0, warn=0):
    if error > 0:
        return "error"
    if warn > 0:
        return "warn"
    return "ok"


# ---------------------------------------------------------------- fields
def check_fields(provider_uri: str, calendar: list,
                 index_codes: set = frozenset(), db_spans: dict | None = None) -> dict:
    """扫描 features/ 目录，检查每只股票 bin 字段完整性与文件长度。

    - 缺失字段文件（BIN_FIELDS 中的某个 .day.bin 不存在）
    - 文件长度 != 4 + 4*len(calendar)（日历对齐破坏，即"首尾重复/错位"bug 的
      结构性信号；正常 bin 恒为整日历长度，IPO 股为 NaN 前缀但长度相同）
    - 读取 close.day.bin 检查 NaN 占比过高与 data[:5]==data[-5:] 重复
      （已知写入 bug 特征）

    Args:
        provider_uri: qlib 数据目录
        calendar: 当前日历（day.txt 日期列表，升序）
        index_codes: 指数代码集合（features/ 下这些目录是 akshare 指数，
            只写 OHLCV，不要求 18 个股票字段，跳过不校验）
        db_spans: stock_daily 每只股票 [最早日期, 最晚日期]（_load_existing_ranges
            返回值）。NaN 占比以此区间内的交易日数为分母：新股上市只有几天，
            整日历占比天然 <1%，会误判为损坏；用真实上市区间后正常股 ≈100%。

    Returns:
        dict: {
            status, message, stocks_checked,
            missing_field_files, missing_field_samples: ["sh600000: close"],
            bad_size_stocks, bad_size_samples, suspicious_bin_stocks,
            suspicious_samples, repair_codes,  # 需要从 PG 重建 bin 的股票
        }
    """
    feat_root = os.path.join(provider_uri, "features")
    from app.services.data.baostock_backfill import BIN_FIELDS

    if not os.path.isdir(feat_root):
        return {
            "status": "warn", "message": "features 目录不存在或为空",
            "stocks_checked": 0, "missing_field_files": 0,
            "missing_field_samples": [], "bad_size_stocks": 0,
            "bad_size_samples": [], "suspicious_bin_stocks": 0,
            "suspicious_samples": [], "repair_codes": [],
        }

    expected_size = QLIB_BIN_HEADER_SIZE + 4 * len(calendar)
    missing_field_files = 0
    missing_field_samples = []
    bad_size_stocks = 0
    bad_size_samples = []
    suspicious_bin_stocks = 0
    suspicious_samples = []
    repair_codes = set()
    stocks_checked = 0

    for name in sorted(os.listdir(feat_root)):
        if name in index_codes:
            continue
        stock_dir = os.path.join(feat_root, name)
        if not os.path.isdir(stock_dir):
            continue
        stocks_checked += 1
        missing = [f for f in BIN_FIELDS if not os.path.exists(os.path.join(stock_dir, f"{f}.day.bin"))]
        if missing:
            missing_field_files += len(missing)
            for f in missing:
                if len(missing_field_samples) < MAX_SAMPLES:
                    missing_field_samples.append(f"{name}: {f}")
            repair_codes.add(name)
            continue

        bad_size = []
        for f in BIN_FIELDS:
            p = os.path.join(stock_dir, f"{f}.day.bin")
            if os.path.getsize(p) != expected_size:
                bad_size.append(f)
        if bad_size:
            bad_size_stocks += 1
            if len(bad_size_samples) < MAX_SAMPLES:
                bad_size_samples.append(f"{name}: {','.join(bad_size[:5])}")
            repair_codes.add(name)

        # 仅对 close 数组做质量检查（顺序读，峰值内存约 2MB）
        close_path = os.path.join(stock_dir, "close.day.bin")
        try:
            import numpy as np
            raw = np.fromfile(close_path, dtype="<f4")
            if raw.size > 0:
                values = raw[1:]  # 去掉 start_index 头
                finite = int(np.count_nonzero(np.isfinite(values)))
                # NaN 占比以"该股实际上市区间内的交易日数"为分母，而不是整条日历：
                # 新股上市只有几天，整日历占比天然 <1% 会被误判为损坏；用
                # stock_daily 的 [min_date, max_date] 求期望交易日数，正常股
                # ratio≈1，只有写入 bug（全 NaN/整段错位）才会 ≈0。
                expected_days = None
                if db_spans and name in db_spans:
                    lo, hi = db_spans[name]
                    expected_days = sum(1 for d in calendar if lo <= d <= hi)
                denom = expected_days if (expected_days or 0) >= 1 else len(values)
                ratio = finite / max(denom, 1)
                duplicated = (
                    len(values) >= 10
                    and bool(np.array_equal(values[:5], values[-5:]))
                )
                if duplicated or ratio < 0.05:
                    suspicious_bin_stocks += 1
                    if len(suspicious_samples) < MAX_SAMPLES:
                        suspicious_samples.append(
                            f"{name} (finite={ratio:.0%}, dup={duplicated}, exp={expected_days or '-'})"
                        )
                    # 全 NaN / 首尾重复 = 写入 bug，需要重建
                    if duplicated or ratio < 0.01:
                        repair_codes.add(name)
        except Exception as e:  # noqa: BLE001
            logger.warning("读取 close bin 失败 %s: %s", close_path, e)

    message = (
        f"扫描 {stocks_checked} 只股票，缺失字段文件 {missing_field_files} 个，"
        f"长度异常 {bad_size_stocks} 只，疑似损坏 {suspicious_bin_stocks} 只"
    )
    return {
        "status": _status_from_counts(error=missing_field_files + bad_size_stocks, warn=suspicious_bin_stocks),
        "message": message,
        "stocks_checked": stocks_checked,
        "missing_field_files": missing_field_files,
        "missing_field_samples": missing_field_samples,
        "bad_size_stocks": bad_size_stocks,
        "bad_size_samples": bad_size_samples,
        "suspicious_bin_stocks": suspicious_bin_stocks,
        "suspicious_samples": suspicious_samples,
        "repair_codes": sorted(repair_codes),
    }


# ------------------------------------------------------------- fieldset
def check_fieldset() -> dict:
    """校验 stock_daily 数据列 ⊆ bin 字段（qlib 包含 DB 全部字段）。

    change/tradable 是 bin 独有的衍生字段（DB 无对应列），预期允许。
    """
    from app.services.data.baostock_backfill import BIN_FIELDS

    db_columns = sorted(
        c.name for c in StockDaily.__table__.columns if c.name not in ("code", "trade_date")
    )
    missing_in_bin = [c for c in db_columns if c not in BIN_FIELDS]
    derived_expected = [f for f in BIN_FIELDS if f not in db_columns]
    status = "error" if missing_in_bin else "ok"
    message = (
        f"数据库 {len(db_columns)} 列，bin {len(BIN_FIELDS)} 字段"
        + (f"，缺失 {missing_in_bin}" if missing_in_bin else "，bin 覆盖数据库全部字段")
    )
    return {
        "status": status,
        "message": message,
        "db_columns": db_columns,
        "bin_fields": BIN_FIELDS,
        "missing_in_bin": missing_in_bin,
        "derived_expected": derived_expected,
    }


# ------------------------------------------------------------- calendar
def _exclude_pending_today(missing_dates: list, now: "datetime | None" = None) -> list:
    """排除"今天但 baostock 尚未发布"的日期。

    baostock 的交易日历提前发布（周末/节假日前即可查到未来交易日），
    但当日日 K 数据要等收盘后 quant_data_update_time（默认 18:00，
    官方 17:30 完成入库 + 30 分钟缓冲）才更新。因此盘中/收盘后未到
    更新时间点时，"缺今天"不是真实缺口，不应提示"需 baostock 补拉"，
    否则会误导触发无意义的 repair/回填（今天的数据根本还不存在）。
    过了发布点仍缺失 → 保留（真缺口）。
    """
    from datetime import datetime

    now = now or datetime.now()
    if not missing_dates:
        return missing_dates
    today = now.date().strftime("%Y-%m-%d")
    if today not in missing_dates:
        return missing_dates
    try:
        update_time = settings.scheduler.quant_data_update_time or "18:00"
        hh, mm = str(update_time).split(":")
        cutoff = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    except (ValueError, AttributeError):
        cutoff = now.replace(hour=18, minute=0, second=0, microsecond=0)
    if now < cutoff:
        return [d for d in missing_dates if d != today]
    return missing_dates


def _calendar_diff(day_txt: set, stock_daily: set, trade_cal: set) -> dict:
    """纯函数：三套日期集合的差异。

    Returns:
        dict: 计数 + 样本（'YYYY-MM-DD' 字符串列表）
    """
    missing_in_day_txt = sorted(stock_daily - day_txt)
    missing_in_stock_daily = sorted(day_txt - stock_daily)
    pg_missing_dates = sorted(trade_cal - stock_daily)
    return {
        "missing_in_day_txt": missing_in_day_txt,
        "missing_in_stock_daily": missing_in_stock_daily,
        "pg_missing_dates": pg_missing_dates,
    }


async def check_calendar(provider_uri: str) -> dict:
    """校验 day.txt 与数据库交易日、baostock 交易日历的一致性。"""
    from app.services.data.eod_incremental import _get_calendar

    day_txt = set(_get_calendar(provider_uri))
    async with async_session() as session:
        sd_rows = await session.execute(select(StockDaily.trade_date).distinct())
        stock_daily_dates = {r[0].strftime("%Y-%m-%d") for r in sd_rows}
        tc_rows = await session.execute(
            select(TradeCalendar.trade_date).where(TradeCalendar.is_trading_day.is_(True))
        )
        trade_cal = {r[0].strftime("%Y-%m-%d") for r in tc_rows}

    diff = _calendar_diff(day_txt, stock_daily_dates, trade_cal)
    missing_in_day_txt = diff["missing_in_day_txt"]
    missing_in_stock_daily = diff["missing_in_stock_daily"]
    # 今天的日期若在 baostock 发布时间点前出现，是"尚未发布"而非缺口，过滤掉
    pg_missing_dates = _exclude_pending_today(diff["pg_missing_dates"])

    status = _status_from_counts(
        error=len(missing_in_day_txt) + len(missing_in_stock_daily),
        warn=len(pg_missing_dates),
    )
    message = (
        f"day.txt {len(day_txt)} 天 / stock_daily {len(stock_daily_dates)} 天 / "
        f"baostock 日历 {len(trade_cal)} 天"
        + (f"，day.txt 缺 {len(missing_in_day_txt)} 天" if missing_in_day_txt else "")
        + (f"，stock_daily 缺 {len(missing_in_stock_daily)} 天" if missing_in_stock_daily else "")
        + (f"，PG 缺 {len(pg_missing_dates)} 个交易日（需 baostock）" if pg_missing_dates else "")
    )
    return {
        "status": status,
        "message": message,
        "counts": {
            "day_txt_count": len(day_txt),
            "stock_daily_count": len(stock_daily_dates),
            "trade_calendar_count": len(trade_cal),
            "missing_in_day_txt": len(missing_in_day_txt),
            "missing_in_stock_daily": len(missing_in_stock_daily),
            "pg_missing_dates": len(pg_missing_dates),
        },
        "missing_in_day_txt_samples": _clip(missing_in_day_txt),
        "missing_in_stock_daily_samples": _clip(missing_in_stock_daily),
        "pg_missing_date_samples": _clip(pg_missing_dates),
    }


# -------------------------------------------------------------- coverage
def _scan_bin_dirs(provider_uri: str) -> set:
    """列出 features/ 下的股票目录（sync，供 run_io_cpu 调用）。"""
    feat_root = os.path.join(provider_uri, "features")
    if not os.path.isdir(feat_root):
        return set()
    return {n for n in os.listdir(feat_root) if os.path.isdir(os.path.join(feat_root, n))}


def _compute_range_mismatch(provider_uri: str, calendar: list,
                            db_ranges: dict, bin_dirs: set) -> list:
    """区间错位：bin 数组长度对应的日历末日期 < 数据库最大日期（sync，线程池执行）。"""
    if not calendar or not db_ranges:
        return []
    from app.services.data.eod_incremental import _read_bin
    cal_len = len(calendar)
    mismatch = []
    for code_lower, (_db_min, db_max) in db_ranges.items():
        if code_lower not in bin_dirs:
            continue
        values, _start = _read_bin(
            os.path.join(provider_uri, "features", code_lower, "close.day.bin")
        )
        if values is None or len(values) == 0:
            mismatch.append(code_lower)
            continue
        n = len(values)
        bin_end = calendar[min(n - 1, cal_len - 1)]
        if db_max > bin_end:
            mismatch.append(code_lower)
    return sorted(set(mismatch))


async def check_coverage(provider_uri: str, calendar: list,
                         index_codes: set = frozenset(), db_ranges: dict | None = None) -> dict:
    """校验数据库每股数据区间与 bin 覆盖是否一致。

    index_codes 里的指数目录（features/ 下的指数，DB 无对应记录）不参与比对，
    避免被误判为 bin_without_db / 区间错位。db_ranges 可由调用方传入（避免
    与 check_fields 重复查询 stock_daily），为空时内部自行加载。
    """
    from app.services.data.baostock_backfill import _load_existing_ranges

    # 数据库每只股票 [min_date, max_date]
    if db_ranges is None:
        db_ranges = await _load_existing_ranges(provider_uri, calendar)
    # bin 股票目录（文件系统，剔除指数目录）
    bin_dirs = {
        c for c in await run_io_cpu(_scan_bin_dirs, provider_uri)
        if c not in index_codes
    }

    db_without_bin = sorted(c for c in db_ranges if c not in bin_dirs)
    bin_without_db = sorted(c for c in bin_dirs if c not in db_ranges)

    range_mismatch = await run_io_cpu(
        _compute_range_mismatch, provider_uri, calendar, db_ranges, bin_dirs
    )
    status = _status_from_counts(error=len(db_without_bin) + len(range_mismatch), warn=len(bin_without_db))
    message = (
        f"DB {len(db_ranges)} 只 / bin {len(bin_dirs)} 只"
        + (f"，DB 无 bin {len(db_without_bin)} 只" if db_without_bin else "")
        + (f"，区间错位 {len(range_mismatch)} 只" if range_mismatch else "")
        + (f"，bin 无 DB 记录 {len(bin_without_db)} 只" if bin_without_db else "")
    )
    return {
        "status": status,
        "message": message,
        "stocks_in_db": len(db_ranges),
        "stocks_in_bin": len(bin_dirs),
        "db_without_bin": len(db_without_bin),
        "db_without_bin_samples": _clip(db_without_bin),
        "bin_without_db": len(bin_without_db),
        "bin_without_db_samples": _clip(bin_without_db),
        "range_mismatch": len(range_mismatch),
        "range_mismatch_samples": _clip(range_mismatch),
        "repair_codes": db_without_bin + range_mismatch,
    }


# ----------------------------------------------------------------- qlib
async def check_qlib(provider_uri: str, universe: str) -> dict:
    """复用 check_integrity 的抽样加载，验证 qlib 端到端可读。"""
    from app.services.data.integrity_check import check_integrity

    result = await run_io_cpu(check_integrity, provider_uri, universe)
    status = "ok" if result.get("ok") else "error"
    return {
        "status": status,
        "message": result.get("summary") or result.get("error") or "",
        "rows": result.get("rows", 0),
        "total_stocks": result.get("total_stocks", 0),
        "columns": result.get("columns", []),
    }


# -------------------------------------------------------------- macro
def check_macro(provider_uri: str, calendar: list,
                index_codes: set = frozenset(), fin_codes: set | None = None) -> dict:
    """校验宏观/财报 bin 字段：文件存在 + 长度与日历对齐（广播字段，抽样股票检查）。

    宏观字段（$pmi 等）全市场同一数组，财报字段（$roe 等）逐股不同，
    但都是广播到 features/*/{field}.day.bin，长度必须与 day.txt 一致，
    否则 qlib 读位错位、因子全是 NaN。取前 N 只股票为样本检查。

    Args:
        provider_uri: qlib 数据目录
        calendar: 当前日历
        index_codes: 指数代码集合（指数无财报数据，跳过不校验）
        fin_codes: 有财报数据入库的股票代码集合（financial_indicator 的 code
            去重）。某股票若已有财报数据但个别字段缺失（如银行无流动/速动比率），
            是数据源固有缺口，bin 无法凭空生成，不应计为"缺失"误报；只有
            完全无财报数据的股票，其字段缺失才是真实缺口（需重新拉取）。
    """
    from app.services.data.macro_sync import MACRO_INDICATORS, AKSHARE_INDICATORS
    from app.services.data.fundamental_sync import FIN_FIELD_NAMES

    macro_fields = [
        fname
        for cfg in MACRO_INDICATORS.values() for fname in cfg["fields"]
    ] + [
        fname
        for cfg in AKSHARE_INDICATORS.values() for fname in cfg["fields"]
    ]
    macro_fields += FIN_FIELD_NAMES
    macro_fields = sorted(set(macro_fields))
    if not macro_fields:
        return {"status": "warn", "message": "无宏观/财报字段配置", "macro_fields": 0,
                "checked_stocks": 0, "missing": 0, "missing_samples": [],
                "bad_size": 0, "bad_size_samples": []}

    expected_size = QLIB_BIN_HEADER_SIZE + 4 * len(calendar)
    feat_root = os.path.join(provider_uri, "features")
    stock_dirs = sorted(os.listdir(feat_root))[:20] if os.path.isdir(feat_root) else []
    missing_count = 0
    bad_size_count = 0
    missing_samples: list[str] = []
    bad_size_samples: list[str] = []
    checked = 0
    for code in stock_dirs:
        d = os.path.join(feat_root, code)
        if not os.path.isdir(d):
            continue
        if code in index_codes:
            continue
        checked += 1
        for f in macro_fields:
            p = os.path.join(d, f"{f}.day.bin")
            if not os.path.exists(p):
                # 财报字段：该股已有财报数据但个别字段缺失（如银行无流动/速动比率）
                # 是数据源固有缺口；只有完全没有财报数据的股票才是真实缺口
                if f in FIN_FIELD_NAMES and fin_codes is not None and code in fin_codes:
                    continue
                missing_count += 1
                if len(missing_samples) < MAX_SAMPLES:
                    missing_samples.append(f"{code}: {f}")
                continue
            if os.path.getsize(p) != expected_size:
                bad_size_count += 1
                if len(bad_size_samples) < MAX_SAMPLES:
                    bad_size_samples.append(f"{code}: {f}")

    status = "ok"
    if missing_count or bad_size_count:
        status = "error"
    message = (
        f"宏观+财报字段 {len(macro_fields)} 个（抽样 {checked} 只股票）"
        + (f"，缺失 {missing_count} 个" if missing_count else "")
        + (f"，长度异常 {bad_size_count} 个" if bad_size_count else "")
        + ("，与日历对齐" if not missing_count and not bad_size_count else "")
    )
    return {
        "status": status,
        "message": message,
        "macro_fields": len(macro_fields),
        "checked_stocks": checked,
        "missing": missing_count,
        "missing_samples": missing_samples,
        "bad_size": bad_size_count,
        "bad_size_samples": bad_size_samples,
    }


# ------------------------------------------------------------ sync state
async def _load_sync_state(universe: str) -> dict:
    """读取 StockDataStatus，判断是否处于回填中。"""
    async with async_session() as session:
        rows = (await session.execute(select(StockDataStatus))).scalars().all()
    if not rows:
        return {"status": "empty", "universe": universe, "latest_date": None, "stock_count": 0, "syncing": False}
    target = next((r for r in rows if r.universe == universe), None) or rows[-1]
    return {
        "status": target.status or "unknown",
        "universe": target.universe,
        "latest_date": target.latest_date,
        "stock_count": target.stock_count or 0,
        "syncing": target.status == "syncing",
    }


# ------------------------------------------------------------ orchestrator
async def run_validation(provider_uri: str | None = None, universe: str = "all") -> dict:
    """主入口：全市场 + 数据库跨存储校验，返回结构化报告。

    Args:
        provider_uri: qlib 数据目录（默认 settings.qlib_provider_path）
        universe: 股票池（默认 all；csi300/csi500 在 qlib 抽检时使用）
    """
    import datetime

    provider_uri = provider_uri or settings.qlib_provider_path
    from app.services.data.eod_incremental import _get_calendar

    calendar = _get_calendar(provider_uri)
    sync_state = await _load_sync_state(universe)

    # 全局活跃同步兜底：状态行只记录指定 universe 的行，回填可能写在其他 universe
    # （如当前回填写在 csi300 行而校验查 all），或状态行被 recovery 误标 failed。
    # 用共享进度文件检测真正在跑的 bin 写入任务，否则回填期间的瞬态错位
    # （bin 对齐 global_calendar 而 day.txt 还在增长）会被误判成真实损坏
    # （全市场"长度异常/疑似损坏"），用户看到虚假告警还会去点「一键补齐」撞 409。
    # 排除 repair：repair 自身的 run_validation 需要真实 drift 来决定修复内容。
    from app.services.data.sync_progress import get_progress, sync_is_active

    active_prog = get_progress()
    if active_prog and sync_is_active() and active_prog.get("data_source") != "repair":
        sync_state["syncing"] = True
        sync_state["status"] = "syncing"

    # 指数主表：features/ 下的指数目录（如 sh000001/sz399001）只写 OHLCV，
    # 不要求 18 个股票字段，也不在 stock_daily/财报中，校验时需跳过。
    from app.models.fundamental import FinancialIndicator
    from app.services.data.index_registry import load_index_codes

    index_codes = await load_index_codes()
    async with async_session() as session:
        fin_rows = await session.execute(select(FinancialIndicator.code).distinct())
        fin_codes = {r[0].lower() for r in fin_rows}

    # stock_daily 每只股票 [min,max]（check_fields 计算新股 ratio 分母 / check_coverage 复用）
    from app.services.data.baostock_backfill import _load_existing_ranges

    db_ranges = await _load_existing_ranges(provider_uri, calendar)

    fields_result = await run_io_cpu(check_fields, provider_uri, calendar, index_codes, db_ranges)
    fieldset_result = check_fieldset()
    calendar_result = await check_calendar(provider_uri)
    coverage_result = await check_coverage(provider_uri, calendar, index_codes, db_ranges)
    qlib_result = await check_qlib(provider_uri, universe)
    macro_result = await run_io_cpu(check_macro, provider_uri, calendar, index_codes, fin_codes)

    # 回填中：字段/覆盖/qlib/宏观结果不可信，统一降级为 warn
    if sync_state["syncing"]:
        _all_results = {
            "fields": fields_result, "coverage": coverage_result,
            "qlib": qlib_result, "macro": macro_result,
        }
        for key in ("fields", "coverage", "qlib", "macro"):
            result = _all_results[key]
            if result["status"] == "error":
                result["status"] = "warn"
                result["message"] += "（回填进行中，结果可能不完整）"

    calendar_counts = calendar_result["counts"]
    drift = {
        "needs_repair": bool(
            calendar_counts["missing_in_day_txt"]
            or calendar_counts["missing_in_stock_daily"]
            or fields_result["missing_field_files"]
            or fields_result["bad_size_stocks"]
            or coverage_result["db_without_bin"]
            or coverage_result["range_mismatch"]
            or macro_result["missing"]
            or macro_result["bad_size"]
        ),
        "missing_field_files": fields_result["missing_field_files"],
        "db_without_bin": coverage_result["db_without_bin"],
        "bin_without_db": coverage_result["bin_without_db"],
        "range_mismatch": coverage_result["range_mismatch"],
        "missing_calendar_days": calendar_counts["missing_in_day_txt"],
        "needs_baostock": bool(calendar_counts["pg_missing_dates"]),
        "pg_missing_dates": calendar_counts["pg_missing_dates"],
        "pg_missing_date_samples": calendar_result["pg_missing_date_samples"],
        "stocks_with_gaps": fields_result["suspicious_bin_stocks"],
        "macro_missing": macro_result["missing"],
        "macro_bad_size": macro_result["bad_size"],
    }
    if sync_state["syncing"]:
        # 回填进行中结果不可信，禁止触发补齐（前端按 drift.needs_repair 隐藏按钮）
        drift["needs_repair"] = False

    check_statuses = [fieldset_result["status"], fields_result["status"],
                      calendar_result["status"], coverage_result["status"],
                      qlib_result["status"], macro_result["status"]]
    all_ok = all(s == "ok" for s in check_statuses)
    summary = "数据完整" if all_ok else (
        "存在待修复差异，可点击「一键补齐」" if drift["needs_repair"] else "存在差异（需人工处理）"
    )
    if sync_state["syncing"]:
        summary = "回填进行中，校验结果不完整"

    # 数据对齐摘要：bin ↔ day.txt ↔ stock_daily ↔ 宏观字段 长度一致性，
    # 对齐正常则因子可直接计算（补齐的作用就是修复对齐错位）。
    align_issues = []
    if fields_result["bad_size_stocks"]:
        align_issues.append(f"bin 长度异常 {fields_result['bad_size_stocks']} 只")
    if calendar_counts["missing_in_day_txt"] or calendar_counts["missing_in_stock_daily"]:
        align_issues.append("day.txt 与 stock_daily 不一致")
    if coverage_result["range_mismatch"]:
        align_issues.append(f"区间错位 {coverage_result['range_mismatch']} 只")
    if macro_result["bad_size"]:
        align_issues.append(f"宏观字段长度异常 {macro_result['bad_size']} 个")
    if macro_result["missing"]:
        align_issues.append(f"宏观字段缺失 {macro_result['missing']} 个")
    checks_summary = "数据对齐正常，因子可直接计算" if not align_issues else "数据对齐待修复：" + "；".join(align_issues[:4])
    if sync_state["syncing"]:
        checks_summary = "回填进行中，数据对齐状态待定"

    return {
        "ok": all_ok and not sync_state["syncing"],
        "summary": summary,
        "checks_summary": checks_summary,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "provider_uri": provider_uri,
        "universe": universe,
        "sync_state": sync_state,
        "calendar": calendar_counts,
        "checks": {
            "fields": fields_result,
            "fieldset": fieldset_result,
            "calendar": calendar_result,
            "coverage": coverage_result,
            "qlib": qlib_result,
            "macro": macro_result,
        },
        "drift": drift,
        # 兼容旧前端字段
        "rows": qlib_result["rows"],
        "columns": qlib_result["columns"],
        "total_stocks": qlib_result["total_stocks"],
        "error": None,
    }
