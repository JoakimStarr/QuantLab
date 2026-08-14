"""全球宏观指标同步：FRED/CFTC/EIA 一手官方源 → PG 窄表 → qlib bin（广播 forward-fill）。

与 macro_sync（东财/akshare 国内宏观）同构、共用 macro_indicator 窄表与广播原语，
区别仅在数据源与拉取函数：

  - FRED（api.stlouisfed.org，需 FRED_API_KEY）：美国联邦基金利率/欧央行利率/CPI/失业率/ISM PMI/非农
  - CFTC（publicreporting.cftc.gov Socrata，无需 key）：黄金/铜/原油 非商业净持仓
  - EIA（api.eia.gov v2，需 EIA_API_KEY）：美国商业原油库存

流程（与 macro_sync 一致）：
  1. fetch_*: 拉取并归一化为 macro_indicator 窄表行（source=fred/cftc/eia）
  2. upsert_global_macro(): 幂等写入（ON CONFLICT DO NOTHING）
  3. forward_fill_to_daily + broadcast_to_all_stocks：按 available_date(PIT) forward-fill
     成日频并广播到全部股票 bin

设计要点：
  - 全局字段全市场同一数组（与国内宏观一致），广播到 features/{code}/{field}.day.bin
  - 缺 API key 时拉取降级为 return []（记 warning），不中断整体
  - 手动触发（无自动同步），符合项目惯例
"""
import asyncio
import logging
import re
from datetime import date, timedelta

from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import async_session
from app.core.executor import run_io_cpu
from app.models.macro import MacroIndicator
from app.services.data.broadcast_state import broadcast_up_to_date, mark_broadcast
from app.services.data.data_clean import to_float as _to_float
from app.services.data.db_utils import bulk_upsert
from app.services.data.eod_incremental import _get_calendar
from app.services.data.macro_sync import (
    _load_all_macro_series,
    broadcast_to_all_stocks,
    forward_fill_to_daily,
)
from app.services.data.sync_progress import (
    clear_progress,
    finish_progress,
    init_progress,
    update_progress,
)

logger = logging.getLogger(__name__)

# 全球宏观注册表：type=fred/cftc/eia，fields 为窄表字段名 → 源参数
# field 配置: {series_id(FRED/EIA) | market(CFTC), units(FRED 可选), label, unit}
GLOBAL_MACRO_INDICATORS: dict[str, dict] = {
    "FRED_RATES": {
        "type": "fred",
        "delay": 0,
        "fields": {
            "us_fed_rate": {"series_id": "DFF", "label": "美国联邦基金利率", "unit": "%"},
            "ecb_rate": {"series_id": "ECBDFR", "label": "欧央行存款便利利率", "unit": "%"},
        },
    },
    "FRED_INFLATION": {
        "type": "fred",
        "delay": 30,
        "fields": {
            # CPIAUCSL 为 CPI 指数（level），units=pc1 直接返回同比 %
            "us_cpi_yoy": {"series_id": "CPIAUCSL", "units": "pc1", "label": "美国CPI同比", "unit": "%"},
            "us_unrate": {"series_id": "UNRATE", "label": "美国失业率", "unit": "%"},
        },
    },
    "FRED_GROWTH": {
        "type": "fred",
        "delay": 30,
        "fields": {
            "us_ism_pmi": {"series_id": "NAPM", "label": "美国ISM制造业PMI", "unit": ""},
            "us_nonfarm": {"series_id": "PAYEMS", "label": "美国非农就业人数", "unit": "千人"},
        },
    },
    "CFTC_COT": {
        "type": "cftc",
        "delay": 3,
        "fields": {
            "gold_cot_net": {"market": "GOLD - COMMODITY EXCHANGE INC.", "label": "黄金非商业净多", "unit": "手"},
            "copper_cot_net": {"market": "COPPER- #1 - COMMODITY EXCHANGE INC.", "label": "铜非商业净多", "unit": "手"},
            "crude_cot_net": {
                "market": "CRUDE OIL, LIGHT SWEET - NEW YORK MERCANTILE EXCHANGE",
                "label": "WTI原油非商业净多", "unit": "手",
            },
        },
    },
    "EIA_CRUDE": {
        "type": "eia",
        "delay": 7,
        "fields": {
            "us_crude_stock": {"series_id": "WGTSTUS1", "label": "美国商业原油库存", "unit": "千桶"},
        },
    },
}

_FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
_CFTC_BASE = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
_EIA_BASE = "https://api.eia.gov/v2/petroleum/stoc/wstk/data/"


def _iso_date(value) -> date | None:
    """解析 ISO 日期（"YYYY-MM-DD" 或带时间后缀 "YYYY-MM-DDT..."）→ date。"""
    if not value:
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(value).strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _mk_row(indicator_key: str, field_name: str, d: date, value: float,
            unit: str | None, delay: int, source: str) -> dict:
    """构造 macro_indicator 窄表行（与 macro_sync 同构）。"""
    return {
        "indicator": indicator_key,
        "report_date": d,
        "field_name": field_name,
        "value": value,
        "unit": unit,
        "available_date": d + timedelta(days=delay),
        "source": source,
    }


def _fetch_fred(indicator_key: str, cfg: dict) -> list[dict]:
    """拉取 FRED 各序列并归一化。同步阻塞（网络 IO），调用方须经 run_io_cpu。"""
    import requests

    key = settings.fred_api_key
    if not key:
        logger.warning("未配置 FRED_API_KEY，跳过 %s", indicator_key)
        return []
    delay = cfg.get("delay", 0)
    rows: list[dict] = []
    for field_name, fcfg in cfg["fields"].items():
        params = {
            "series_id": fcfg["series_id"],
            "api_key": key,
            "file_type": "json",
            "observation_start": "2000-01-01",
        }
        if fcfg.get("units"):
            params["units"] = fcfg["units"]
        try:
            resp = requests.get(_FRED_BASE, params=params, timeout=20)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            logger.warning("FRED %s 拉取失败: %s", fcfg["series_id"], e)
            continue
        for obs in payload.get("observations") or []:
            d = _iso_date(obs.get("date"))
            if d is None:
                continue
            val = _to_float(obs.get("value"))
            if val is None:
                continue
            rows.append(_mk_row(indicator_key, field_name, d, val, fcfg.get("unit"), delay, "fred"))
    logger.info("FRED %s 归一化 %d 行", indicator_key, len(rows))
    return rows


def _fetch_cftc(indicator_key: str, cfg: dict) -> list[dict]:
    """拉取 CFTC 期货-only 持仓报告，非商业净多 = noncomm long - short。"""
    import requests

    delay = cfg.get("delay", 0)
    rows: list[dict] = []
    for field_name, fcfg in cfg["fields"].items():
        params = {
            "market_and_exchange_names": fcfg["market"],
            "$order": "report_date_as_yyyy_mm_dd DESC",
            "$limit": 5000,
        }
        try:
            resp = requests.get(_CFTC_BASE, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning("CFTC %s 拉取失败: %s", fcfg["market"], e)
            continue
        for rec in data:
            d = _iso_date(rec.get("report_date_as_yyyy_mm_dd"))
            if d is None:
                continue
            long_v = _to_float(rec.get("noncomm_positions_long_all"))
            short_v = _to_float(rec.get("noncomm_positions_short_all"))
            if long_v is None or short_v is None:
                continue
            rows.append(_mk_row(indicator_key, field_name, d, long_v - short_v,
                                fcfg.get("unit"), delay, "cftc"))
    logger.info("CFTC %s 归一化 %d 行", indicator_key, len(rows))
    return rows


def _fetch_eia(indicator_key: str, cfg: dict) -> list[dict]:
    """拉取 EIA v2 周度原油库存。同步阻塞，调用方须经 run_io_cpu。"""
    import requests

    key = settings.eia_api_key
    if not key:
        logger.warning("未配置 EIA_API_KEY，跳过 %s", indicator_key)
        return []
    delay = cfg.get("delay", 0)
    rows: list[dict] = []
    for field_name, fcfg in cfg["fields"].items():
        params = {
            "api_key": key,
            "frequency": "weekly",
            "data[0]": "value",
            "facets[series][]": fcfg["series_id"],
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "offset": 0,
            "length": 5000,
        }
        try:
            resp = requests.get(_EIA_BASE, params=params, timeout=30)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            logger.warning("EIA %s 拉取失败: %s", fcfg["series_id"], e)
            continue
        series = (payload.get("response") or {}).get("data") or []
        for rec in series:
            d = _iso_date(rec.get("period"))
            if d is None:
                continue
            val = _to_float(rec.get("value"))
            if val is None:
                continue
            rows.append(_mk_row(indicator_key, field_name, d, val, fcfg.get("unit"), delay, "eia"))
    logger.info("EIA %s 归一化 %d 行", indicator_key, len(rows))
    return rows


async def upsert_global_macro(rows: list[dict]) -> int:
    """幂等写入 macro_indicator 窄表（ON CONFLICT DO NOTHING）。"""
    return await bulk_upsert(
        MacroIndicator, rows, ["indicator", "report_date", "field_name"], batch=500,
    )


async def _fetch_all_global_macro_rows(progress_cb=None) -> tuple[list[dict], dict]:
    """拉取全部全球宏观指标 → 归一化窄表行。"""
    all_rows: list[dict] = []
    summary: dict[str, int] = {}
    ind_order = list(GLOBAL_MACRO_INDICATORS)
    total_ind = len(ind_order)
    for i, indicator_key in enumerate(ind_order):
        if progress_cb:
            progress_cb(
                5 + int(35 * i / total_ind),
                f"拉取全球宏观指标 {i + 1}/{total_ind}（{indicator_key}）...",
            )
        cfg = GLOBAL_MACRO_INDICATORS[indicator_key]
        typ = cfg.get("type")
        if typ == "fred":
            rows = await run_io_cpu(_fetch_fred, indicator_key, cfg)
        elif typ == "cftc":
            rows = await run_io_cpu(_fetch_cftc, indicator_key, cfg)
        elif typ == "eia":
            rows = await run_io_cpu(_fetch_eia, indicator_key, cfg)
        else:
            logger.warning("未知全球宏观类型 %s，跳过 %s", typ, indicator_key)
            rows = []
        all_rows.extend(rows)
        summary[indicator_key] = len(rows)
        logger.info("全球宏观 %s 拉取 %d 行", indicator_key, len(rows))
    return all_rows, summary


async def _global_macro_fingerprint() -> dict:
    """macro_indicator 表中全球宏观指标聚合指纹：行数 + 最新可用日。"""
    async with async_session() as session:
        result = await session.execute(
            select(func.count(), func.max(MacroIndicator.available_date))
            .where(MacroIndicator.indicator.in_(list(GLOBAL_MACRO_INDICATORS)))
        )
        count, max_d = result.one()
    return {"count": count or 0, "max_available": max_d.strftime("%Y-%m-%d") if max_d else None}


async def broadcast_global_macro_to_bins(provider_uri: str, progress_cb=None,
                                         force: bool = False) -> int:
    """把 PG 窄表的全球宏观数据 forward-fill 广播写入全部股票 bin 字段。

    与 macro_sync.broadcast_macro_to_bins 同构：全市场同一数组，日历对齐后写入。
    指纹一致（数据没变且日历没变）时跳过全市场重写。
    """
    qlib_dir = provider_uri or settings.qlib_provider_path
    calendar = await run_io_cpu(_get_calendar, qlib_dir)
    fp = {"cal_len": len(calendar), "cal_end": calendar[-1] if calendar else None}
    fp.update(await _global_macro_fingerprint())
    if not force and await asyncio.to_thread(broadcast_up_to_date, qlib_dir, "global_macro", fp):
        logger.info("全球宏观字段无变化（日历 %s 天），跳过广播", fp["cal_len"])
        return 0

    all_field_specs = [
        (ind, fname, fcfg)
        for ind, cfg in GLOBAL_MACRO_INDICATORS.items() for fname, fcfg in cfg["fields"].items()
    ]
    total_fields = len(all_field_specs)
    total_written = 0
    series_map = await _load_all_macro_series()
    for j, (indicator_key, field_name, _fcfg) in enumerate(all_field_specs):
        if progress_cb:
            progress_cb(
                45 + int(55 * (j + 1) / total_fields),
                f"广播全球宏观字段 {j + 1}/{total_fields}（{field_name}）...",
            )
        series = series_map.get((indicator_key, field_name))
        if series is None or series.empty:
            logger.warning("全球宏观字段 %s.%s 无数据，跳过", indicator_key, field_name)
            continue
        values = await run_io_cpu(forward_fill_to_daily, qlib_dir, field_name, series, calendar)
        n = await run_io_cpu(broadcast_to_all_stocks, qlib_dir, field_name, values)
        total_written += n
        logger.info("全球宏观 %s.%s 广播写入 %d 只股票", indicator_key, field_name, n)
    await asyncio.to_thread(mark_broadcast, qlib_dir, "global_macro", fp)
    return total_written


async def sync_global_macro(provider_uri: str | None = None, broadcast: bool = True,
                            progress_cb=None) -> dict:
    """全球宏观同步主入口：抓取 → 入库 →（可选）forward-fill 广播写 bin。

    broadcast=False（fetch-only）：只拉数据入库 PG，不写 bin、不碰全局进度。
    broadcast=True：拉取 + 广播，带全局进度（数据校验/补齐阶段调用）。
    """
    qlib_dir = provider_uri or settings.qlib_provider_path

    if not broadcast:
        all_rows, summary = await _fetch_all_global_macro_rows()
        inserted = await upsert_global_macro(all_rows)
        logger.info("全球宏观拉取完成（仅入库）: 新增 %d 行", inserted)
        return {"ok": True, "source": "fred+cftc+eia", "inserted": inserted,
                "fields_written": 0, "by_indicator": summary}

    owns_progress = progress_cb is None
    report = progress_cb or (lambda pct, msg: update_progress(pct=pct, status="running", message=msg))
    if owns_progress:
        init_progress("global_macro", "fred", writes_bins=True, kind="global_macro")
    summary: dict[str, int] = {}
    try:
        all_rows, summary = await _fetch_all_global_macro_rows(progress_cb=report)
        report(42, "写入数据库...")
        inserted = await upsert_global_macro(all_rows)
        logger.info("全球宏观入库: 新增 %d 行", inserted)

        total_written = await broadcast_global_macro_to_bins(qlib_dir, progress_cb=report)

        if owns_progress:
            finish_progress(True)
            await asyncio.sleep(3)
            clear_progress()
        return {"ok": True, "source": "fred+cftc+eia", "inserted": inserted,
                "fields_written": total_written, "by_indicator": summary}
    except Exception as e:
        if owns_progress:
            finish_progress(False, str(e))
            await asyncio.sleep(3)
            clear_progress()
        logger.exception("全球宏观同步失败")
        raise


async def run_global_macro_sync_task(broadcast: bool = False) -> None:
    """后台任务包装：同步全球宏观指标并更新状态。默认只拉数据入库。"""
    try:
        result = await sync_global_macro(broadcast=broadcast)
        logger.info("全球宏观同步后台任务完成: %s", result)
    except Exception as e:
        logger.exception("全球宏观同步后台任务失败: %s", e)
