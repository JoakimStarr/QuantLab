"""季频财报同步：akshare 财务摘要 → PG 窄表 → qlib bin（PIT forward-fill 广播）。

与 macro_sync 同构，但按股票逐只（各股数值不同，不能全市场广播同一数组）：
- 拉取：akshare ``stock_financial_abstract``（按股一次返回 ~107 季度 × 80 指标，
  全市场 ~5400 次请求，约 2-3 小时）。baostock 季频财报按股×季（全市场 10 年
  约 130 万次请求）远超日限额，不可行。
- 入库：financial_indicator 窄表（code, report_date, field_name, value,
  available_date）。available_date = 报告期 + 法定披露截止延迟（近似 pub_date，
  防 look-ahead，与宏观指标 delay 做法一致）。
- 广播：按 available_date(PIT) forward-fill 写 features/{code}/{field}.day.bin，
  供 qlib 因子直接引用（如 $roe / $netprofit_yoy）。

fetch-only（broadcast=False）不碰全局同步进度：可在回填期间触发，安全。
bin 广播依赖最终日历，应在数据校验/补齐阶段触发。
"""
import asyncio
import logging
import math
import os
import random
import time
from datetime import date

import pandas as pd
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import async_session
from app.core.executor import run_io_cpu
from app.models.fundamental import FinancialIndicator
from app.services.data.broadcast_state import broadcast_up_to_date, mark_broadcast
from app.services.data.data_clean import to_float as _to_float
from app.services.data.db_utils import bulk_upsert
from app.services.data.eod_incremental import _get_calendar, _write_bin

logger = logging.getLogger(__name__)

# akshare 财务摘要指标 → bin 字段名（factor 引用 $field）
# akshare 列名（中文）→ {field: qlib bin 字段, label, unit}
FIN_INDICATORS: dict[str, dict] = {
    "归母净利润": {"field": "netprofit", "unit": "元", "label": "归母净利润"},
    "营业总收入": {"field": "revenue", "unit": "元", "label": "营业总收入"},
    "扣非净利润": {"field": "netprofit_deduct", "unit": "元", "label": "扣非净利润"},
    "净资产收益率(ROE)": {"field": "roe", "unit": "%", "label": "净资产收益率"},
    "总资产报酬率(ROA)": {"field": "roa", "unit": "%", "label": "总资产报酬率"},
    "毛利率": {"field": "gross_margin", "unit": "%", "label": "毛利率"},
    "销售净利率": {"field": "net_margin", "unit": "%", "label": "销售净利率"},
    "资产负债率": {"field": "debt_ratio", "unit": "%", "label": "资产负债率"},
    "经营现金流量净额": {"field": "ocf", "unit": "元", "label": "经营现金流量净额"},
    "基本每股收益": {"field": "eps", "unit": "元", "label": "基本每股收益"},
    "每股净资产": {"field": "bvps", "unit": "元", "label": "每股净资产"},
    "营业总收入增长率": {"field": "revenue_yoy", "unit": "%", "label": "营业总收入同比增长率"},
    "归属母公司净利润增长率": {"field": "netprofit_yoy", "unit": "%", "label": "归母净利润同比增长率"},
    "经营活动净现金/归属母公司的净利润": {"field": "ocf_to_np", "unit": "倍", "label": "经营净现金/归母净利润"},
    "流动比率": {"field": "current_ratio", "unit": "倍", "label": "流动比率"},
    "速动比率": {"field": "quick_ratio", "unit": "倍", "label": "速动比率"},
    "权益乘数": {"field": "equity_multiplier", "unit": "倍", "label": "权益乘数"},
}

# bin 广播字段名（唯一）
FIN_FIELD_NAMES: list[str] = sorted({cfg["field"] for cfg in FIN_INDICATORS.values()})

# 指标中文名 → field 反查（校验/取数用）
_INDICATOR_NAME_TO_FIELD = {name: cfg["field"] for name, cfg in FIN_INDICATORS.items()}


def _compute_available_date(stat: date) -> date:
    """法定披露截止日近似 pub_date（防 look-ahead）。

    A 股财报披露截止：一季报 4/30、中报 8/31、三季报 10/31、年报次年 4/30。
    """
    if stat.month == 3:
        return stat.replace(month=4, day=30)
    if stat.month == 6:
        return stat.replace(month=8, day=31)
    if stat.month == 9:
        return stat.replace(month=10, day=31)
    if stat.month == 12:
        return stat.replace(year=stat.year + 1, month=4, day=30)
    return stat


def expected_latest_report_date(today: date | None = None) -> date | None:
    """按 A 股财报披露周期，判断"今天应已披露的最新报告期"。

    财报是季频数据（4 个报告期/年），不需要每天拉。以法定披露截止日为准：
    - 一季报(3/31) 截止 4/30
    - 中报(6/30)   截止 8/31
    - 三季报(9/30) 截止 10/31
    - 年报(12/31)  截止次年 4/30

    返回"披露截止日已过"的最新报告期截止日（按截止日从晚到早遍历）。
    已披露到该报告期的股票视为最新，无需重拉；未到该报告期的才需要拉取。
    这使财报同步只在新的披露窗口到来时重跑，而不是每次全同步都全市场拉一遍。

    例：2026-06-15 → 一季报(2026-03-31) 已截止，返回 2026-03-31（年报也截止，
    但一季报更新，取一季报）。2026-01-10 → 去年三季报(2025-09-30) 已截止，
    返回 2025-09-30（去年年报尚未截止，不要求）。
    """
    today = today or date.today()
    y = today.year
    candidates = [
        # (报告期, 披露截止日)，截止日从晚到早
        (date(y, 9, 30), date(y, 10, 31)),        # 今年三季报
        (date(y, 6, 30), date(y, 8, 31)),         # 今年中报
        (date(y, 3, 31), date(y, 4, 30)),         # 今年一季报
        (date(y - 1, 12, 31), date(y, 4, 30)),    # 去年年报（次年 4/30 截止）
        (date(y - 1, 9, 30), date(y - 1, 10, 31)),   # 去年三季报
        (date(y - 1, 6, 30), date(y - 1, 8, 31)),    # 去年中报
        (date(y - 1, 3, 31), date(y - 1, 4, 30)),    # 去年一季报
    ]
    for report, deadline in candidates:
        if today >= deadline:
            return report
    return None  # 理论上不会发生（candidates 覆盖最近两年）


def _fetch_stock_financial(qlib_code: str, retries: int = 2) -> list[dict]:
    """拉取单只股票的财务摘要（akshare），宽表转长表归一化为窄表行。

    反爬：网络抖动/临时限流时指数退避重试（2s → 4s），仍失败返回空。

    Args:
        qlib_code: sh600000（akshare 用 600000，去掉交易所前缀）
        retries: 失败重试次数（指数退避）
    Returns:
        窄表行列表 [(code, report_date, field_name, value, unit, available_date)]
    """
    import akshare as ak

    symbol = qlib_code[2:] if len(qlib_code) >= 8 else qlib_code
    df = None
    for attempt in range(retries + 1):
        try:
            df = ak.stock_financial_abstract(symbol=symbol)
            break
        except Exception as e:
            if attempt < retries:
                wait = 2 ** attempt  # 2s, 4s
                logger.warning("akshare 财务摘要失败 %s（第 %d 次，%ds 后重试）: %s",
                               qlib_code, attempt + 1, wait, str(e)[:100])
                time.sleep(wait)
            else:
                logger.warning("akshare 财务摘要失败 %s（重试耗尽）: %s", qlib_code, str(e)[:120])
    if df is None or df.empty or "指标" not in df.columns:
        return []

    date_cols = [c for c in df.columns if str(c).isdigit() and len(str(c)) == 8]
    rows: list[dict] = []
    for _, r in df.iterrows():
        name = str(r.get("指标", "")).strip()
        field = _INDICATOR_NAME_TO_FIELD.get(name)
        if field is None:
            continue
        for col in date_cols:
            val = _to_float(r.get(col))
            if val is None:
                continue
            try:
                stat = date(int(col[:4]), int(col[4:6]), int(col[6:]))
            except (ValueError, IndexError):
                continue
            rows.append({
                "code": qlib_code,
                "report_date": stat,
                "field_name": field,
                "value": val,
                "unit": FIN_INDICATORS[name].get("unit"),
                "available_date": _compute_available_date(stat),
                "source": "akshare",
            })
    logger.info("财务摘要 %s → %d 行", qlib_code, len(rows))
    return rows


async def upsert_financial(rows: list[dict]) -> int:
    """幂等写入 financial_indicator 窄表（ON CONFLICT DO NOTHING）。"""
    return await bulk_upsert(
        FinancialIndicator, rows, ["code", "report_date", "field_name"], batch=500,
    )


# ------------------------------------------------------------ 广播（PIT）
def _forward_fill_series(series: pd.Series, cal_dates: pd.DatetimeIndex) -> "object":
    """把某股票某字段的（available_date, value）序列 forward-fill 到日历。"""
    s = series[~series.index.duplicated(keep="last")].sort_index()
    if s.empty:
        return None
    daily = s.reindex(cal_dates, method="ffill")
    import numpy as np
    return np.nan_to_num(daily.values.astype(np.float32), nan=np.nan)


async def _load_all_series() -> dict:
    """一次加载全部财报数据 → {(code, field): pd.Series(index=available_date)}。"""
    async with async_session() as session:
        result = await session.execute(
            select(
                FinancialIndicator.code,
                FinancialIndicator.field_name,
                FinancialIndicator.available_date,
                FinancialIndicator.value,
            )
        )
        rows = result.all()
    df = pd.DataFrame(rows, columns=["code", "field", "date", "value"]).dropna(subset=["date", "value"])
    if df.empty:
        return {}
    out: dict = {}
    for (code, field), g in df.groupby(["code", "field"]):
        s = g.set_index("date")["value"].sort_index()
        out[(code, field)] = s
    return out


async def broadcast_financial_to_bins(provider_uri: str, progress_cb=None,
                                      force: bool = False) -> int:
    """把每只股票的财报序列按 PIT forward-fill 写入各自 bin 字段。

    与宏观不同：财报数值逐股不同，必须每股各自广播（features/{code}/{field}.day.bin）。

    跳过优化：财报源数据（行数/最新报告期）与日历均未变时指纹一致，跳过
    全市场重写；调用方校验发现财报字段缺失/错位时可 force=True 强制。
    Returns: 写入的（股票 × 字段）数。
    """
    qlib_dir = provider_uri or settings.qlib_provider_path
    calendar = _get_calendar(qlib_dir)
    if not calendar:
        logger.warning("日历为空，无法广播财报字段")
        return 0
    fp = {"cal_len": len(calendar), "cal_end": calendar[-1]}
    fp.update(await _financial_fingerprint())
    if not force and await asyncio.to_thread(broadcast_up_to_date, qlib_dir, "fundamental", fp):
        logger.info("财报字段无变化（最新报告期 %s，行数 %s），跳过广播",
                    fp.get("max_report"), fp.get("count"))
        return 0
    cal_dates = pd.to_datetime(calendar)

    series_map = await _load_all_series()
    if not series_map:
        logger.warning("financial_indicator 无数据，跳过广播")
        return 0

    feat_root = os.path.join(qlib_dir, "features")
    if not os.path.isdir(feat_root):
        return 0
    stock_codes = sorted(os.listdir(feat_root))
    total = len(stock_codes)
    written = 0
    for i, code in enumerate(stock_codes):
        code_dir = os.path.join(feat_root, code)
        if not os.path.isdir(code_dir):
            continue
        for field in FIN_FIELD_NAMES:
            series = series_map.get((code, field))
            if series is None:
                continue
            values = _forward_fill_series(series, cal_dates)
            if values is None:
                continue
            _write_bin(os.path.join(code_dir, f"{field}.day.bin"), values, 0)
            written += 1
        if progress_cb and (i % 50 == 0 or i == total - 1):
            progress_cb(i + 1, total, f"广播财报字段 {i + 1}/{total}（{code}）...")
    await asyncio.to_thread(mark_broadcast, qlib_dir, "fundamental", fp)
    logger.info("财报广播完成: %d 股票 × 字段", written)
    return written


async def _financial_fingerprint() -> dict:
    """financial_indicator 表聚合指纹：行数 + 最新报告期。"""
    async with async_session() as session:
        count = (await session.execute(select(func.count()).select_from(FinancialIndicator))).scalar() or 0
        max_d = (await session.execute(select(func.max(FinancialIndicator.report_date)))).scalar()
    return {"count": count, "max_report": max_d.strftime("%Y-%m-%d") if max_d else None}


# ------------------------------------------------------------ 主流程
async def _load_fetched_codes(today: date | None = None) -> set:
    """已覆盖"当前应披露最新报告期"的股票代码集合（增量跳过）。

    财报是季频数据，不需要每次同步全市场重拉。判断口径（二选一，同时满足）：
      1. 字段基本齐全（≥ 一半 FIN_FIELD_NAMES）——防止"部分拉取中毒"
      2. 该股最新报告期 >= 今天应披露的最新报告期——新季度披露窗口到来前，
         即使字段齐全也视为"待更新"，重拉一次即可拿到新季度数据

    银行等少数股票缺流动/速动比率个别字段仍算完整，不会被反复重拉。
    """
    threshold = math.ceil(len(FIN_FIELD_NAMES) / 2)
    expected = expected_latest_report_date(today)
    async with async_session() as session:
        stmt = (
            select(FinancialIndicator.code)
            .group_by(FinancialIndicator.code)
            .having(func.count(func.distinct(FinancialIndicator.field_name)) >= threshold)
        )
        if expected is not None:
            # 最新报告期 >= 应披露的最新报告期才视为已更新（季频：披露窗口外不重拉）
            stmt = stmt.having(func.max(FinancialIndicator.report_date) >= expected)
        result = await session.execute(stmt)
        return {r[0] for r in result.all()}


async def fetch_all_financial(codes: list[str], progress_cb=None) -> tuple[int, int]:
    """逐股拉取财务摘要 → 入库（增量：跳过已入库股票）。

    反爬措施：
    - 随机抖动限频（0.3-0.8s），避免固定节奏被识别
    - 单股重试 + 指数退避（_fetch_stock_financial 内部）
    - 限流熔断：连续 6 只失败 → 暂停 60s 冷却；累计 3 次冷却仍失败 → 中止本次
      （已拉部分已入库，重跑只补漏，不浪费）
    - 增量：已入库的股票跳过，全量重跑成本极低

    Returns: (本次拉取股票数, 新增行数)
    """
    if not codes:
        feat_root = os.path.join(settings.qlib_provider_path, "features")
        if os.path.isdir(feat_root):
            codes = sorted(os.listdir(feat_root))
    if not codes:
        return 0, 0

    # 指数目录没有财报数据（akshare 返回 None），剔除避免逐只重试浪费配额
    from app.services.data.index_registry import load_index_codes

    index_codes = await load_index_codes()
    if index_codes:
        codes = [c for c in codes if c not in index_codes]

    existing = await _load_fetched_codes()
    todo = [c for c in codes if c not in existing]
    skipped = len(codes) - len(todo)
    expected = expected_latest_report_date()
    if skipped:
        logger.info(
            "财报增量跳过 %d 只已覆盖最新报告期（应披露至 %s）的股票，待拉取 %d 只",
            skipped, expected, len(todo),
        )
    if not todo and expected:
        logger.info("财报数据已是最新（覆盖报告期 %s），无需拉取", expected)

    total = len(todo)
    all_rows: list[dict] = []
    inserted = 0
    consecutive_fail = 0
    cooldowns = 0
    fetched = 0
    failed = 0
    # 每 100 只股票落库一次：避免千万行全攒内存（曾 3GB+）且最后一次性
    # commit 无进度、中途崩溃全丢。落库间隔写入进度，前端能看到推进。
    FLUSH_EVERY = 100
    for i, code in enumerate(todo):
        if not os.path.isdir(os.path.join(settings.qlib_provider_path, "features", code)):
            continue
        rows = await run_io_cpu(_fetch_stock_financial, code)
        if rows:
            all_rows.extend(rows)
            consecutive_fail = 0
            fetched += 1
        else:
            failed += 1
            consecutive_fail += 1
            if consecutive_fail >= 6:
                cooldowns += 1
                logger.warning("财报连续 6 只失败（第 %d 次冷却），疑似被限流，暂停 60s", cooldowns)
                if progress_cb:
                    progress_cb(i + 1, total, f"疑似被限流，冷却 60s（第 {i + 1}/{total} 只）...")
                await asyncio.sleep(60)
                consecutive_fail = 0
                if cooldowns >= 3:
                    logger.error("财报拉取多次冷却仍持续失败，中止本次（剩余 %d 只稍后重跑补漏）",
                                 total - i - 1)
                    failed += total - i - 1  # 剩余未拉计入失败
                    break
        # 随机抖动限频：0.3-0.8s，避免固定节奏
        await asyncio.sleep(0.3 + random.uniform(0, 0.5))
        # 定期落库：内存有界 + 进度可见 + 崩溃只丢一小段
        if all_rows and ((i + 1) % FLUSH_EVERY == 0 or i + 1 == total):
            n = await upsert_financial(all_rows)
            inserted += n
            all_rows = []
            if progress_cb and i + 1 < total:
                progress_cb(i + 1, total,
                            f"拉取财报 {i + 1}/{total}（已入库 {inserted} 行）...")

    # 尾批兜底（若循环被 break 提前退出）
    if all_rows:
        inserted += await upsert_financial(all_rows)
    logger.info("财报拉取完成: 本次 %d 只（跳过 %d 只已入库，失败 %d 只）, 新增 %d 行",
                fetched, skipped, failed, inserted)
    return total, inserted


async def run_financial_sync(broadcast: bool = False, codes: list[str] = None,
                             provider_uri: str = None, progress_cb=None) -> dict:
    """财报同步主入口：拉取 → 入库 →（可选）PIT 广播写 bin。

    - broadcast=False（fetch-only）：只拉数据入库。若当前无其他同步任务在跑
      则写全局进度（前端可见）；若有（如回填），静默运行避免覆盖其进度。
    - broadcast=True：拉取 + 广播（数据校验/补齐阶段调用，日历已对齐），带进度。

    progress_cb: 传入时由调用方统一管理全局进度（如一键全同步并行阶段），
        本函数不再 init/finish/clear 共享进度文件，只通过 ``progress_cb(i, n, msg)``
        上报进度——避免多个并行阶段互相覆盖进度文件造成竞态。
    """
    from app.services.data.sync_progress import (
        clear_progress, finish_progress, init_progress, set_worker_pid,
        update_progress, sync_is_active,
    )

    qlib_dir = provider_uri or settings.qlib_provider_path
    # 有外部进度回调（并行阶段）时不操作共享进度文件，避免并行阶段互相覆盖
    owns_progress = progress_cb is None
    use_progress = owns_progress and (broadcast or not sync_is_active())
    if use_progress:
        # broadcast 会写 bin（writes_bins=True），fetch-only 只写 PG（False）
        init_progress("fundamental", "fundamental", writes_bins=broadcast, kind="fundamental")
        # 登记 worker PID：进程被 kill -9 后 web 端 sync_is_active 才能识别
        # "worker 已死"而非永久 409 阻塞（此前无 pid 时残留进度文件恒视为活跃）
        set_worker_pid(os.getpid())

    report = progress_cb or (
        lambda i, n, msg: update_progress(
            pct=5 + int(90 * i / max(n, 1)), status="running", message=msg,
        )
    )

    try:
        if use_progress:
            fetch_cb = (
                (lambda i, n, msg: update_progress(
                    pct=5 + int(35 * i / max(n, 1)), status="running", message=msg,
                ))
                if broadcast else
                report
            )
        else:
            fetch_cb = report if progress_cb else None

        fetched, inserted = await fetch_all_financial(codes or [], progress_cb=fetch_cb)

        fields_written = 0
        if broadcast:
            if owns_progress:
                update_progress(pct=45, status="running", message="广播财报字段到 bin（PIT 对齐）...")
            fields_written = await broadcast_financial_to_bins(
                qlib_dir,
                progress_cb=lambda i, n, msg: update_progress(
                    pct=45 + int(55 * i / max(n, 1)), status="running", message=msg,
                ) if owns_progress else report(i, n, msg),
            )

        if use_progress:
            finish_progress(True)
            await asyncio.sleep(3)
            clear_progress()
        return {"ok": True, "source": "akshare", "inserted": inserted,
                "fetched": fetched, "fields_written": fields_written}
    except Exception as e:
        if use_progress:
            finish_progress(False, str(e))
            await asyncio.sleep(3)
            clear_progress()
        logger.exception("财报同步失败")
        raise
