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
def check_fields(provider_uri: str, calendar: list) -> dict:
    """扫描 features/ 目录，检查每只股票 bin 字段完整性与文件长度。

    - 缺失字段文件（BIN_FIELDS 中的某个 .day.bin 不存在）
    - 文件长度 != 4 + 4*len(calendar)（日历对齐破坏，即"首尾重复/错位"bug 的
      结构性信号；正常 bin 恒为整日历长度，IPO 股为 NaN 前缀但长度相同）
    - 读取 close.day.bin 检查 NaN 占比过高与 data[:5]==data[-5:] 重复
      （已知写入 bug 特征）

    Args:
        provider_uri: qlib 数据目录
        calendar: 当前日历（day.txt 日期列表，升序）

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
                ratio = finite / max(len(values), 1)
                duplicated = (
                    len(values) >= 10
                    and bool(np.array_equal(values[:5], values[-5:]))
                )
                if duplicated or ratio < 0.05:
                    suspicious_bin_stocks += 1
                    if len(suspicious_samples) < MAX_SAMPLES:
                        suspicious_samples.append(
                            f"{name} (finite={ratio:.0%}, dup={duplicated})"
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
    pg_missing_dates = diff["pg_missing_dates"]

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


async def check_coverage(provider_uri: str, calendar: list) -> dict:
    """校验数据库每股数据区间与 bin 覆盖是否一致。"""
    from app.services.data.baostock_backfill import _load_existing_ranges

    # 数据库每只股票 [min_date, max_date]
    db_ranges = await _load_existing_ranges(provider_uri, calendar)
    # bin 股票目录（文件系统）
    bin_dirs = await run_io_cpu(_scan_bin_dirs, provider_uri)

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

    fields_result = await run_io_cpu(check_fields, provider_uri, calendar)
    fieldset_result = check_fieldset()
    calendar_result = await check_calendar(provider_uri)
    coverage_result = await check_coverage(provider_uri, calendar)
    qlib_result = await check_qlib(provider_uri, universe)

    # 回填中：字段/覆盖结果不可信，降级为 warn
    if sync_state["syncing"]:
        for key in ("fields", "coverage"):
            result = fields_result if key == "fields" else coverage_result
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
    }

    check_statuses = [fieldset_result["status"], fields_result["status"],
                      calendar_result["status"], coverage_result["status"],
                      qlib_result["status"]]
    all_ok = all(s == "ok" for s in check_statuses)
    summary = "数据完整" if all_ok else (
        "存在待修复差异，可点击「一键补齐」" if drift["needs_repair"] else "存在差异（需人工处理）"
    )
    if sync_state["syncing"]:
        summary = "回填进行中，校验结果不完整"

    return {
        "ok": all_ok and not sync_state["syncing"],
        "summary": summary,
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
        },
        "drift": drift,
        # 兼容旧前端字段
        "rows": qlib_result["rows"],
        "columns": qlib_result["columns"],
        "total_stocks": qlib_result["total_stocks"],
        "error": None,
    }
