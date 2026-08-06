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
import re
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import settings
from app.core.database import async_session
from app.core.executor import run_io_cpu
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

# akshare 宏观指标注册表（国债/回购/Shibor/LPR/商品/汇率/货币供应/社融/贷款等）。
# 与东财 datacenter 互补，扩展新指标只需在此加一条配置。
# 配置项:
#   ak_func   akshare 函数名
#   ak_kwargs 调用参数，日期占位符 <today> / <Ny>（N=年数，如 <10y>）在拉取时替换（YYYYMMDD 格式）
#   date_col  日期/月份列名
#   date_freq day=日频（date 列） / month=月频（月份列，如 "2026年06月份"、"202604"、"2026-06"）
#   delay     发布延迟（天），PIT 对齐防 look-ahead
#   fields    {field_name: {source: akshare 列名, label, unit}}
#
# 取数窗口：日频指标（国债/期货/汇率）用 <30y>（覆盖 2000 至今），
# 各数据源返回自身实际可提供的范围（沪铜 2005、沪金 2008、原油 2018 起等），
# 超出数据源历史的部分自动缺失，不影响入库（upsert 幂等）。
AKSHARE_INDICATORS: dict[str, dict] = {
    "TREASURY": {
        "ak_func": "bond_zh_us_rate",
        "ak_kwargs": {"start_date": "<30y>"},
        "date_col": "日期",
        "date_freq": "day",
        "delay": 0,
        "fields": {
            "trsy2y": {"source": "中国国债收益率2年", "label": "中债2年期收益率", "unit": "%"},
            "trsy5y": {"source": "中国国债收益率5年", "label": "中债5年期收益率", "unit": "%"},
            "trsy10y": {"source": "中国国债收益率10年", "label": "中债10年期收益率", "unit": "%"},
            "trsy30y": {"source": "中国国债收益率30年", "label": "中债30年期收益率", "unit": "%"},
            "trsy_spread_10y2y": {"source": "中国国债收益率10年-2年", "label": "中债期限利差10Y-2Y", "unit": "%"},
            "us_trsy2y": {"source": "美国国债收益率2年", "label": "美债2年期收益率", "unit": "%"},
            "us_trsy10y": {"source": "美国国债收益率10年", "label": "美债10年期收益率", "unit": "%"},
            "us_trsy_spread": {"source": "美国国债收益率10年-2年", "label": "美债期限利差10Y-2Y", "unit": "%"},
        },
    },
    "REPO_FR": {
        # 银行间回购定盘利率（FR001/FR007/FR014，近似 R 系列）。
        # 走 repo_rate_query 的 Chinamoney CSV，全量历史；不用 repo_rate_hist
        # （该函数传日期范围会触发 akshare KeyError 'frValueMap'）。
        "ak_func": "repo_rate_query",
        "ak_kwargs": {"symbol": "回购定盘利率"},
        "date_col": "date",
        "date_freq": "day",
        "delay": 0,
        "fields": {
            "fr001": {"source": "FR001", "label": "回购定盘利率隔夜", "unit": "%"},
            "fr007": {"source": "FR007", "label": "回购定盘利率7天(近似R007)", "unit": "%"},
            "fr014": {"source": "FR014", "label": "回购定盘利率14天", "unit": "%"},
        },
    },
    "REPO_FDR": {
        # 银银间回购定盘利率（FDR001/FDR007/FDR014，存款类机构口径，近似 DR 系列）。
        "ak_func": "repo_rate_query",
        "ak_kwargs": {"symbol": "银银间回购定盘利率"},
        "date_col": "date",
        "date_freq": "day",
        "delay": 0,
        "fields": {
            "fdr001": {"source": "FDR001", "label": "银银间回购定盘利率隔夜", "unit": "%"},
            "fdr007": {"source": "FDR007", "label": "银银间回购定盘利率7天(近似DR007)", "unit": "%"},
            "fdr014": {"source": "FDR014", "label": "银银间回购定盘利率14天", "unit": "%"},
        },
    },
    "SHIBOR": {
        "ak_func": "macro_china_shibor_all",
        "date_col": "日期",
        "date_freq": "day",
        "delay": 0,
        "fields": {
            "shibor_on": {"source": "O/N-定价", "label": "Shibor隔夜", "unit": "%"},
            "shibor_1w": {"source": "1W-定价", "label": "Shibor1周", "unit": "%"},
            "shibor_3m": {"source": "3M-定价", "label": "Shibor3月", "unit": "%"},
            "shibor_1y": {"source": "1Y-定价", "label": "Shibor1年", "unit": "%"},
        },
    },
    "LPR": {
        "ak_func": "macro_china_lpr",
        "date_col": "TRADE_DATE",
        "date_freq": "day",
        "delay": 0,
        "fields": {
            "lpr1y": {"source": "LPR1Y", "label": "LPR1年期", "unit": "%"},
            "lpr5y": {"source": "LPR5Y", "label": "LPR5年期", "unit": "%"},
        },
    },
    "COMMODITY": {
        "ak_func": "macro_china_commodity_price_index",
        "date_col": "日期",
        "date_freq": "day",
        "delay": 0,
        "fields": {
            "commodity_idx": {"source": "最新值", "label": "中国大宗商品价格指数", "unit": ""},
        },
    },
    "HSGT": {
        # 北向资金（沪深港通）日频：市场级每日，广播全市场同一数组（走宏观管线）
        "ak_func": "stock_hsgt_hist_em",
        "ak_kwargs": {"symbol": "北向资金"},
        "date_col": "日期",
        "date_freq": "day",
        "delay": 1,  # 北向当日盘后公布，+1 天防 look-ahead
        "fields": {
            "hsgt_net_buy": {"source": "当日成交净买额", "label": "北向净买入额", "unit": "亿元"},
            "hsgt_buy": {"source": "买入成交额", "label": "北向买入额", "unit": "亿元"},
            "hsgt_sell": {"source": "卖出成交额", "label": "北向卖出额", "unit": "亿元"},
            "hsgt_cum_net": {"source": "历史累计净买额", "label": "北向历史累计净买额", "unit": "亿元"},
            "hsgt_inflow": {"source": "当日资金流入", "label": "北向当日资金流入", "unit": "亿元"},
            "hsgt_hold_mv": {"source": "持股市值", "label": "北向持股总市值", "unit": "亿元"},
        },
    },
    "COPPER": {
        "ak_func": "futures_main_sina",
        "ak_kwargs": {"symbol": "CU0", "start_date": "<30y>", "end_date": "<today>"},
        "date_col": "日期",
        "date_freq": "day",
        "delay": 0,
        "fields": {
            "copper_close": {"source": "收盘价", "label": "沪铜主力收盘价", "unit": "元/吨"},
        },
    },
    "FX": {
        "ak_func": "currency_boc_sina",
        "ak_kwargs": {"symbol": "美元", "start_date": "<30y>", "end_date": "<today>"},
        "date_col": "日期",
        "date_freq": "day",
        "delay": 0,
        "fields": {
            "usdcny_mid": {"source": "央行中间价", "label": "美元兑人民币中间价", "unit": ""},
        },
    },
    "MONEY_SUPPLY": {
        "ak_func": "macro_china_money_supply",
        "date_col": "月份",
        "date_freq": "month",
        "delay": 10,
        "fields": {
            "m2_yoy": {"source": "货币和准货币(M2)-同比增长", "label": "M2同比", "unit": "%"},
            "m1_yoy": {"source": "货币(M1)-同比增长", "label": "M1同比", "unit": "%"},
            "m0_yoy": {"source": "流通中的现金(M0)-同比增长", "label": "M0同比", "unit": "%"},
        },
    },
    "SOCIAL_FINANCE": {
        "ak_func": "macro_china_shrzgm",
        "date_col": "月份",
        "date_freq": "month",
        "delay": 15,
        "fields": {
            "social_finance": {"source": "社会融资规模增量", "label": "社会融资规模增量", "unit": "亿元"},
            "sf_rmb_loan": {"source": "其中-人民币贷款", "label": "社融中人民币贷款", "unit": "亿元"},
        },
    },
    "LOAN": {
        "ak_func": "macro_rmb_loan",
        "date_col": "月份",
        "date_freq": "month",
        "delay": 10,
        "fields": {
            "new_loan": {"source": "新增人民币贷款-总额", "label": "新增人民币贷款", "unit": "亿元"},
            "new_loan_yoy": {"source": "新增人民币贷款-同比", "label": "新增人民币贷款同比", "unit": "%"},
        },
    },
    "CRUDE_OIL": {
        "ak_func": "futures_main_sina",
        "ak_kwargs": {"symbol": "SC0", "start_date": "<30y>", "end_date": "<today>"},
        "date_col": "日期",
        "date_freq": "day",
        "delay": 0,
        "fields": {
            "crude_close": {"source": "收盘价", "label": "原油SC主力收盘价", "unit": "元/桶"},
        },
    },
    "MARGIN": {
        "ak_func": "macro_china_market_margin_sh",
        "date_col": "日期",
        "date_freq": "day",
        "delay": 0,
        "fields": {
            # 源单位为元（约 1.3 万亿），除以 1e8 存为亿元，展示更可读
            "margin_balance": {"source": "融资融券余额", "label": "沪市两融余额", "unit": "亿元", "scale": 1e-8},
        },
    },
    "MARGIN_SZ": {
        "ak_func": "macro_china_market_margin_sz",
        "date_col": "日期",
        "date_freq": "day",
        "delay": 0,
        "fields": {
            "margin_balance_sz": {"source": "融资融券余额", "label": "深市两融余额", "unit": "亿元", "scale": 1e-8},
        },
    },
    "IVIX": {
        "ak_func": "index_option_50etf_qvix",
        "date_col": "date",
        "date_freq": "day",
        "delay": 0,
        "fields": {
            "ivix": {"source": "close", "label": "50ETF期权波动率指数iVIX", "unit": ""},
        },
    },
    "FUTURES_IF": {
        "ak_func": "futures_main_sina",
        "ak_kwargs": {"symbol": "IF0", "start_date": "<30y>", "end_date": "<today>"},
        "date_col": "日期",
        "date_freq": "day",
        "delay": 0,
        "fields": {
            "if_close": {"source": "收盘价", "label": "沪深300期货IF主力收盘价", "unit": ""},
            "if_hold": {"source": "持仓量", "label": "IF主力持仓量", "unit": "手"},
        },
    },
    "FUTURES_IC": {
        "ak_func": "futures_main_sina",
        "ak_kwargs": {"symbol": "IC0", "start_date": "<30y>", "end_date": "<today>"},
        "date_col": "日期",
        "date_freq": "day",
        "delay": 0,
        "fields": {
            "ic_close": {"source": "收盘价", "label": "中证500期货IC主力收盘价", "unit": ""},
            "ic_hold": {"source": "持仓量", "label": "IC主力持仓量", "unit": "手"},
        },
    },
    "FUTURES_TF": {
        "ak_func": "futures_main_sina",
        "ak_kwargs": {"symbol": "TF0", "start_date": "<30y>", "end_date": "<today>"},
        "date_col": "日期",
        "date_freq": "day",
        "delay": 0,
        "fields": {
            "tf_close": {"source": "收盘价", "label": "国债期货TF主力收盘价", "unit": ""},
        },
    },
    "GOLD": {
        "ak_func": "futures_main_sina",
        "ak_kwargs": {"symbol": "AU0", "start_date": "<30y>", "end_date": "<today>"},
        "date_col": "日期",
        "date_freq": "day",
        "delay": 0,
        "fields": {
            "au_close": {"source": "收盘价", "label": "沪金AU主力收盘价", "unit": "元/克"},
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


def _resolve_ak_kwargs(cfg: dict) -> dict:
    """解析 akshare 调用 kwargs，替换日期占位符（<today> / <Ny>，N=年数）。"""
    kwargs = dict(cfg.get("ak_kwargs") or {})
    today = datetime.now()
    if kwargs.get("end_date") == "<today>":
        kwargs["end_date"] = today.strftime("%Y%m%d")
    start = kwargs.get("start_date")
    if isinstance(start, str):
        m = re.fullmatch(r"<(\d+)y>", start)
        if m:
            years = int(m.group(1))
            kwargs["start_date"] = (today - timedelta(days=365 * years)).strftime("%Y%m%d")
    return kwargs


def _to_float(val) -> float | None:
    """宽松转 float：容忍 %、千分位逗号、None、NaN。"""
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


def _parse_macro_date(value, freq: str) -> date | None:
    """解析 akshare 日期/月份列 → date。

    day 频率: datetime.date / datetime / 'YYYY-MM-DD'
    month 频率: '2026年06月份' / '202604' / '2026-06'（取当月 1 日）
    """
    if value is None:
        return None
    if freq == "month":
        s = str(value).strip()
        m = re.search(r"(\d{4})[年\-/]?(\d{1,2})", s)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), 1)
            except ValueError:
                return None
        m2 = re.search(r"(\d{4})(\d{2})", s)
        if m2:
            try:
                return date(int(m2.group(1)), int(m2.group(2)), 1)
            except ValueError:
                return None
        return None
    try:
        dt = pd.to_datetime(value)
        return dt.date()
    except (ValueError, TypeError):
        return None


def _fetch_akshare_macro(indicator_key: str, cfg: dict) -> list[dict]:
    """调用 akshare 拉取单个指标，归一化为 macro_indicator 窄表行。

    同步阻塞函数（网络 IO），调用方必须经 run_io_cpu 放入线程池。
    """
    import akshare as ak

    fn = getattr(ak, cfg["ak_func"], None)
    if fn is None:
        logger.warning("akshare 无函数 %s", cfg["ak_func"])
        return []
    try:
        df = fn(**_resolve_ak_kwargs(cfg))
    except Exception as e:
        logger.warning("akshare %s 拉取失败: %s", cfg["ak_func"], e)
        return []
    if df is None or df.empty:
        logger.warning("akshare %s 返回空", cfg["ak_func"])
        return []

    delay = cfg.get("delay", 0)
    freq = cfg.get("date_freq", "day")
    rows: list[dict] = []
    for _, r in df.iterrows():
        d = _parse_macro_date(r.get(cfg["date_col"]), freq)
        if d is None:
            continue
        avail = d + timedelta(days=delay)
        for field_name, fcfg in cfg["fields"].items():
            src = fcfg["source"]
            if src not in df.columns:
                continue
            val = _to_float(r.get(src))
            if val is None:
                continue
            # scale: 单位换算（如 元 → 亿元，除以 1e8）
            val = val * fcfg.get("scale", 1.0)
            rows.append({
                "indicator": indicator_key,
                "report_date": d,
                "field_name": field_name,
                "value": val,
                "unit": fcfg.get("unit"),
                "available_date": avail,
                "source": "akshare",
            })
    logger.info("akshare %s 归一化 %d 行", indicator_key, len(rows))
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


async def _fetch_all_macro_rows(progress_cb=None) -> tuple[list[dict], dict]:
    """拉取全部宏观指标（东财 datacenter + akshare）→ 归一化窄表行。

    Args:
        progress_cb: 可选进度回调 progress_cb(pct, message)；None 时不报进度
            （fetch-only 模式可能在其他同步进行中触发，不能碰全局进度）
    Returns:
        (rows, summary): rows 为窄表行列表，summary 为 {indicator: 拉取行数}
    """
    all_rows: list[dict] = []
    summary: dict[str, int] = {}
    ind_order = list(MACRO_INDICATORS) + list(AKSHARE_INDICATORS)
    total_ind = len(ind_order)
    for i, indicator_key in enumerate(ind_order):
        if progress_cb:
            progress_cb(
                5 + int(35 * i / total_ind),
                f"拉取宏观指标 {i + 1}/{total_ind}（{indicator_key}）...",
            )
        if indicator_key in MACRO_INDICATORS:
            cfg = MACRO_INDICATORS[indicator_key]
            df = await run_io_cpu(fetch_eastmoney_macro, cfg["report_name"])
            rows = _build_macro_rows(df, indicator_key) if not df.empty else []
        else:
            rows = await run_io_cpu(_fetch_akshare_macro, indicator_key, AKSHARE_INDICATORS[indicator_key])
        all_rows.extend(rows)
        summary[indicator_key] = len(rows)
        logger.info("宏观 %s 拉取 %d 行", indicator_key, len(rows))
    return all_rows, summary


async def broadcast_macro_to_bins(provider_uri: str, progress_cb=None) -> int:
    """把 PG 窄表的宏观数据 forward-fill 广播写入全部股票 bin 字段。

    与拉取解耦：bin 广播依赖最终日历（day.txt 对齐 bin 长度），
    应在数据校验/补齐阶段（日历已定）调用，避免回填期间写入错位长度。

    Args:
        provider_uri: qlib 数据目录
        progress_cb: 可选进度回调 progress_cb(pct, message)，调用方决定是否上报
    Returns:
        写成功的股票数（跨全部字段累计）
    """
    qlib_dir = provider_uri or settings.qlib_provider_path
    all_field_specs = [
        (ind, fname, fcfg)
        for ind, cfg in MACRO_INDICATORS.items() for fname, fcfg in cfg["fields"].items()
    ] + [
        (ind, fname, fcfg)
        for ind, cfg in AKSHARE_INDICATORS.items() for fname, fcfg in cfg["fields"].items()
    ]
    total_fields = len(all_field_specs)
    total_written = 0
    for j, (indicator_key, field_name, fcfg) in enumerate(all_field_specs):
        if progress_cb:
            progress_cb(
                45 + int(55 * (j + 1) / total_fields),
                f"广播字段 {j + 1}/{total_fields}（{field_name}）...",
            )
        series = await _load_macro_series(indicator_key, field_name)
        if series.empty:
            logger.warning("宏观字段 %s.%s 无数据，跳过", indicator_key, field_name)
            continue
        values = await run_io_cpu(forward_fill_to_daily, qlib_dir, field_name, series)
        n = await run_io_cpu(broadcast_to_all_stocks, qlib_dir, field_name, values)
        total_written += n
        logger.info("宏观 %s.%s 广播写入 %d 只股票", indicator_key, field_name, n)
    return total_written


async def sync_macro_indicators(provider_uri: str | None = None, broadcast: bool = True) -> dict:
    """宏观指标同步主入口：抓取 → 入库 →（可选）forward-fill 广播写 bin。

    broadcast=False（fetch-only，API 默认）：只拉数据入库 PG，不写 bin，
    **不碰全局同步进度**——可能在其他同步（如 baostock 回填）进行中触发，
    写共享进度文件会覆盖/清除回填的进度显示与 sync_is_active 状态。

    broadcast=True（数据校验/补齐阶段调用）：拉取 + 广播，带全局进度；
    此时通常无其他同步在跑，日历也已对齐到最终长度。
    """
    qlib_dir = provider_uri or settings.qlib_provider_path

    # fetch-only：不走全局进度，避免与运行中的回填互相覆盖
    if not broadcast:
        all_rows, summary = await _fetch_all_macro_rows()
        inserted = await upsert_macro(all_rows)
        logger.info("宏观拉取完成（仅入库）: 新增 %d 行", inserted)
        return {"ok": True, "source": "eastmoney+akshare", "inserted": inserted,
                "fields_written": 0, "by_indicator": summary}

    init_progress("macro", "eastmoney", writes_bins=True, kind="macro")
    summary: dict[str, int] = {}
    try:
        all_rows, summary = await _fetch_all_macro_rows(
            progress_cb=lambda pct, msg: update_progress(pct=pct, status="running", message=msg),
        )
        update_progress(pct=42, status="running", message="写入数据库...")
        inserted = await upsert_macro(all_rows)
        logger.info("宏观入库: 新增 %d 行", inserted)

        total_written = await broadcast_macro_to_bins(
            qlib_dir,
            progress_cb=lambda pct, msg: update_progress(pct=pct, status="running", message=msg),
        )

        finish_progress(True)
        await asyncio.sleep(3)
        clear_progress()
        return {"ok": True, "source": "eastmoney+akshare", "inserted": inserted,
                "fields_written": total_written, "by_indicator": summary}
    except Exception as e:
        finish_progress(False, str(e))
        await asyncio.sleep(3)
        clear_progress()
        logger.exception("宏观同步失败")
        raise


async def run_macro_sync_task(broadcast: bool = False) -> None:
    """后台任务包装：同步宏观指标并更新状态。

    默认只拉数据入库（broadcast=False），bin 广播由数据校验/补齐阶段触发。
    """
    try:
        result = await sync_macro_indicators(broadcast=broadcast)
        logger.info("宏观同步后台任务完成: %s", result)
    except Exception as e:
        logger.exception("宏观同步后台任务失败: %s", e)
