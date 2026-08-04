"""宏观指标同步：东财 datacenter → PG 窄表 → qlib bin（广播 forward-fill）。

流程：
  1. fetch_eastmoney_macro(): 按 report_name 拉取东财 datacenter 宏观指标
     （支持 jQuery 包裹 JSON 与纯 JSON），归一化为行列表
  2. upsert_macro(): 幂等写入 macro_indicator 窄表（ON CONFLICT DO NOTHING）
  3. forward_fill_to_daily(): 按 available_date(PIT) 对日历 forward-fill 成日频，
     广播写入 features/{code}/{field}.day.bin（复用 eod_incremental._write_bin）

设计要点：
  - 通用窄表：任意指标任意字段，扩展新指标只需在 MACRO_INDICATORS 加配置
  - PIT 对齐：available_date = REPORT_DATE + 发布延迟（防 look-ahead）
  - 手动触发（无自动同步），符合项目惯例
"""
import asyncio
import json
import logging
import os
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import settings
from app.core.database import async_session
from app.models.macro import MacroIndicator
from app.services.data.eod_incremental import _get_calendar, _write_bin
from app.services.data.sync_progress import (
    init_progress, update_progress, finish_progress, clear_progress,
)

logger = logging.getLogger(__name__)

# 东财 datacenter 宏观指标注册表
# field 配置: {source: 东财列名, delay: 发布延迟(天), label: 中文名, unit: 单位}
MACRO_INDICATORS: dict[str, dict] = {
    "PMI": {
        "report_name": "RPT_ECONOMY_PMI",
        "fields": {
            "pmi": {"source": "MAKE_INDEX", "delay": 0, "label": "制造业PMI", "unit": ""},
            "pmi_nm": {"source": "NMAKE_INDEX", "delay": 0, "label": "非制造业PMI", "unit": ""},
        },
    },
    "CPI": {
        "report_name": "RPT_ECONOMY_CPI",
        "fields": {
            "cpi": {"source": "NATIONAL_SAME", "delay": 9, "label": "CPI同比", "unit": "%"},
        },
    },
    "PPI": {
        "report_name": "RPT_ECONOMY_PPI",
        "fields": {
            "ppi": {"source": "BASE_SAME", "delay": 9, "label": "PPI同比", "unit": "%"},
        },
    },
    "GDP": {
        "report_name": "RPT_ECONOMY_GDP",
        "fields": {
            "gdp": {"source": "SUM_SAME", "delay": 45, "label": "GDP同比", "unit": "%"},
        },
    },
}

_EASTMONEY_BASE = (
    "https://datacenter-web.eastmoney.com/api/data/v1/get"
    "?columns=ALL&sortColumns=REPORT_DATE&sortTypes=-1&source=WEB&client=WEB"
    "&reportName={report_name}&pageSize=2000"
)


def _parse_eastmoney_response(text: str) -> dict | None:
    """解析东财响应：可能为 jQuery 包裹 JSON 或纯 JSON，返回 result dict。"""
    if not text:
        return None
    s = text.strip()
    if s.startswith("jQuery"):
        # jQuery1123...(...);  → 取第一对括号内容
        start = s.find("(")
        end = s.rfind(")")
        if start < 0 or end < 0 or end <= start:
            return None
        s = s[start + 1:end]
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def fetch_eastmoney_macro(report_name: str) -> pd.DataFrame:
    """拉取单个东财宏观指标（同步函数，放线程池执行）。

    Returns:
        DataFrame: 列含 REPORT_DATE + 该 report 全部源列，日期为 datetime。
        请求失败或 result 为空时返回空 DataFrame。
    """
    import requests

    url = _EASTMONEY_BASE.format(report_name=report_name)
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    parsed = _parse_eastmoney_response(resp.text)
    if not parsed or parsed.get("message") != "ok" or not parsed.get("result"):
        logger.warning("东财宏观 %s 无数据: %s", report_name, str(parsed)[:200])
        return pd.DataFrame()
    rows = parsed["result"].get("data") or []
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["REPORT_DATE"] = pd.to_datetime(df["REPORT_DATE"])
    return df


def _build_macro_rows(df: pd.DataFrame, indicator_key: str) -> list[dict]:
    """把东财 DataFrame 归一化为 macro_indicator 窄表行。"""
    cfg = MACRO_INDICATORS[indicator_key]
    # 过滤 REPORT_DATE 缺失/NaT 的行
    df = df.dropna(subset=["REPORT_DATE"])
    rows = []
    for _, r in df.iterrows():
        report_date = r["REPORT_DATE"].date()
        for field_name, fcfg in cfg["fields"].items():
            src_col = fcfg["source"]
            if src_col not in df.columns:
                continue
            val = r.get(src_col)
            if val is None or pd.isna(val):
                continue
            rows.append({
                "indicator": indicator_key,
                "report_date": report_date,
                "field_name": field_name,
                "value": float(val),
                "unit": fcfg.get("unit"),
                "available_date": report_date + timedelta(days=fcfg.get("delay", 0)),
                "source": "eastmoney",
            })
    return rows


async def upsert_macro(rows: list[dict]) -> int:
    """幂等写入 macro_indicator 窄表（ON CONFLICT DO NOTHING）。"""
    if not rows:
        return 0
    inserted = 0
    async with async_session() as session:
        for i in range(0, len(rows), 500):
            chunk = rows[i:i + 500]
            stmt = pg_insert(MacroIndicator.__table__).values(chunk)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["indicator", "report_date", "field_name"]
            )
            res = await session.execute(stmt)
            inserted += res.rowcount or 0
        await session.commit()
    return inserted


async def _load_macro_series(indicator: str, field_name: str) -> pd.Series:
    """从窄表读取某指标字段的全部 (available_date, value)，按日期升序。"""
    async with async_session() as session:
        result = await session.execute(
            select(MacroIndicator.available_date, MacroIndicator.value)
            .where(
                MacroIndicator.indicator == indicator,
                MacroIndicator.field_name == field_name,
                MacroIndicator.available_date.isnot(None),
                MacroIndicator.value.isnot(None),
            )
            .order_by(MacroIndicator.available_date)
        )
        rows = result.all()
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series(
        [float(v) for _, v in rows],
        index=pd.to_datetime([d for d, _ in rows]),
    )
    # 同 available_date 去重（保留最后一个）
    return s[~s.index.duplicated(keep="last")].sort_index()


def forward_fill_to_daily(provider_uri: str, field_name: str, series: pd.Series) -> np.ndarray:
    """把月度序列按日历 forward-fill 成日频数组（长度=日历长度）。

    Args:
        provider_uri: qlib 数据目录（读 calendars/day.txt）
        field_name: 仅用于日志
        series: 索引为 available_date(datetime) 的月频序列，值已排序

    Returns:
        np.ndarray[float32]: 与日历等长的数组，available_date 当天起生效并持续到下一个值
    """
    calendar = _get_calendar(provider_uri)
    if not calendar:
        logger.warning("日历为空，无法 forward-fill %s", field_name)
        return np.array([], dtype=np.float32)
    if series.empty:
        return np.full(len(calendar), np.nan, dtype=np.float32)

    cal_dates = pd.to_datetime(calendar)
    # reindex 到日历：月频值落在日历日期上，其余 NaN；再 forward-fill
    daily = series.reindex(cal_dates, method="ffill")
    values = daily.values.astype(np.float32)
    return np.nan_to_num(values, nan=np.nan)


def broadcast_to_all_stocks(provider_uri: str, field_name: str, values: np.ndarray) -> int:
    """把日频宏观数组广播写入所有现存股票的 features/{code}/{field}.day.bin。

    遍历 features/*/ 目录（只写已存在的股票，不新建），复用 _write_bin。
    Returns: 写成功的股票数
    """
    feat_root = os.path.join(provider_uri, "features")
    if not os.path.isdir(feat_root) or len(values) == 0:
        return 0
    written = 0
    for code in os.listdir(feat_root):
        code_dir = os.path.join(feat_root, code)
        if not os.path.isdir(code_dir):
            continue
        _write_bin(os.path.join(code_dir, f"{field_name}.day.bin"), values, 0)
        written += 1
    return written


async def sync_macro_indicators(provider_uri: str | None = None) -> dict:
    """宏观指标同步主入口：抓取 → 入库 → forward-fill → 广播写 bin。

    注意：广播写 bin 会遍历 features/*/ 数千个目录做文件 IO，
    必须经 run_io_cpu 放到线程池执行，绝不能在事件循环里直接跑
    （否则 /health 等所有请求都会卡住）。
    """
    from app.core.executor import run_io_cpu

    qlib_dir = provider_uri or settings.qlib_provider_path
    init_progress("macro", "eastmoney")

    summary: dict[str, int] = {}
    try:
        all_rows: list[dict] = []
        for indicator_key in MACRO_INDICATORS:
            cfg = MACRO_INDICATORS[indicator_key]
            df = await run_io_cpu(fetch_eastmoney_macro, cfg["report_name"])
            rows = _build_macro_rows(df, indicator_key) if not df.empty else []
            all_rows.extend(rows)
            summary[indicator_key] = len(rows)
            logger.info("宏观 %s 拉取 %d 行", indicator_key, len(rows))

        inserted = await upsert_macro(all_rows)
        logger.info("宏观入库: 新增 %d 行", inserted)

        # forward-fill + 广播写 bin（同步 IO 重活走线程池）
        total_written = 0
        for indicator_key, cfg in MACRO_INDICATORS.items():
            for field_name, fcfg in cfg["fields"].items():
                series = await _load_macro_series(indicator_key, field_name)
                if series.empty:
                    logger.warning("宏观字段 %s.%s 无数据，跳过", indicator_key, field_name)
                    continue
                values = await run_io_cpu(forward_fill_to_daily, qlib_dir, field_name, series)
                n = await run_io_cpu(broadcast_to_all_stocks, qlib_dir, field_name, values)
                total_written += n
                logger.info("宏观 %s.%s 广播写入 %d 只股票", indicator_key, field_name, n)

        finish_progress(True)
        await asyncio.sleep(3)
        clear_progress()
        return {"ok": True, "source": "eastmoney", "inserted": inserted,
                "fields_written": total_written, "by_indicator": summary}
    except Exception as e:
        finish_progress(False, str(e))
        await asyncio.sleep(3)
        clear_progress()
        logger.exception("宏观同步失败")
        raise


async def run_macro_sync_task() -> None:
    """后台任务包装：同步宏观指标并更新状态。"""
    try:
        result = await sync_macro_indicators()
        logger.info("宏观同步后台任务完成: %s", result)
    except Exception as e:
        logger.exception("宏观同步后台任务失败: %s", e)
