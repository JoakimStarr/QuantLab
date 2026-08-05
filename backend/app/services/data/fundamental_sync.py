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
from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import settings
from app.core.database import async_session
from app.core.executor import run_io_cpu
from app.models.fundamental import FinancialIndicator
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


def _to_float(val) -> float | None:
    """宽松转 float：容忍 None/NaN/千分位逗号。"""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        try:
            return float(val) if not pd.isna(val) else None
        except (TypeError, ValueError):
            return None
    s = str(val).strip().replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


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
    if not rows:
        return 0
    inserted = 0
    async with async_session() as session:
        for i in range(0, len(rows), 500):
            chunk = rows[i:i + 500]
            stmt = pg_insert(FinancialIndicator.__table__).values(chunk)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["code", "report_date", "field_name"]
            )
            res = await session.execute(stmt)
            inserted += res.rowcount or 0
        await session.commit()
    return inserted


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


async def broadcast_financial_to_bins(provider_uri: str, progress_cb=None) -> int:
    """把每只股票的财报序列按 PIT forward-fill 写入各自 bin 字段。

    与宏观不同：财报数值逐股不同，必须每股各自广播（features/{code}/{field}.day.bin）。
    Returns: 写入的（股票 × 字段）数。
    """
    qlib_dir = provider_uri or settings.qlib_provider_path
    calendar = _get_calendar(qlib_dir)
    if not calendar:
        logger.warning("日历为空，无法广播财报字段")
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
    logger.info("财报广播完成: %d 股票 × 字段", written)
    return written


# ------------------------------------------------------------ 主流程
async def _load_fetched_codes() -> set:
    """已完整入库的股票代码集合（增量跳过：重跑只补缺失，减少请求量）。

    只把"字段基本齐全"的股票视为已入库：部分拉取（如网络中断只写入了
    netprofit 一个字段）的股票不在此列，下次重跑会重新拉取补齐，避免
    字段永久缺失（"部分拉取中毒"）。阈值取一半字段数：银行等少数股票
    缺流动/速动比率等个别字段仍算完整，不会被反复重拉。
    """
    threshold = math.ceil(len(FIN_FIELD_NAMES) / 2)
    async with async_session() as session:
        result = await session.execute(
            select(FinancialIndicator.code)
            .group_by(FinancialIndicator.code)
            .having(func.count(func.distinct(FinancialIndicator.field_name)) >= threshold)
        )
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
    if skipped:
        logger.info("财报增量跳过 %d 只已入库股票，待拉取 %d 只", skipped, len(todo))

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
                             provider_uri: str = None) -> dict:
    """财报同步主入口：拉取 → 入库 →（可选）PIT 广播写 bin。

    - broadcast=False（fetch-only）：只拉数据入库。若当前无其他同步任务在跑
      则写全局进度（前端可见）；若有（如回填），静默运行避免覆盖其进度。
    - broadcast=True：拉取 + 广播（数据校验/补齐阶段调用，日历已对齐），带进度。
    """
    from app.services.data.sync_progress import (
        clear_progress, finish_progress, init_progress, update_progress, sync_is_active,
    )

    qlib_dir = provider_uri or settings.qlib_provider_path
    # fetch-only 在无其他活跃同步时才写进度；broadcast 总是写
    use_progress = broadcast or not sync_is_active()
    if use_progress:
        # broadcast 会写 bin（writes_bins=True），fetch-only 只写 PG（False）
        init_progress("fundamental", "fundamental", writes_bins=broadcast)

    try:
        if use_progress:
            fetch_cb = (
                (lambda i, n, msg: update_progress(
                    pct=5 + int(35 * i / max(n, 1)), status="running", message=msg,
                ))
                if broadcast else
                (lambda i, n, msg: update_progress(
                    pct=5 + int(90 * i / max(n, 1)), status="running", message=msg,
                ))
            )
        else:
            fetch_cb = None

        fetched, inserted = await fetch_all_financial(codes or [], progress_cb=fetch_cb)

        fields_written = 0
        if broadcast:
            update_progress(pct=45, status="running", message="广播财报字段到 bin（PIT 对齐）...")
            fields_written = await broadcast_financial_to_bins(
                qlib_dir,
                progress_cb=lambda i, n, msg: update_progress(
                    pct=45 + int(55 * i / max(n, 1)), status="running", message=msg,
                ),
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
