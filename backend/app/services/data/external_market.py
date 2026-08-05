"""外盘隔夜情绪因子：拉取全球指数，对齐 A股日历后广播成 qlib bin 字段。

数据源：akshare（免费、国内可用）
- 美股：``index_us_stock_sina``（.INX 标普500 / .IXIC 纳斯达克 / .DJI 道琼斯）
- 港股：``stock_hk_index_daily_em``（HSI 恒生指数）

因子含义：A股某交易日 T 的外盘因子 = "T 开盘前最近一次已收盘的外盘交易日 u
（u < T）的 close-to-close 涨跌幅"。美股收盘在北京凌晨 4-5 点、港股收盘在前日
16:00，均早于 A股 9:30 开盘，无未来函数。

字段写入 features/{code}/{field}.day.bin（复用 macro_sync.broadcast_to_all_stocks），
如 ``us_sp500_ret`` / ``hk_hsi_ret``，可在因子表达式中以 ``$us_sp500_ret`` 引用。
"""
import asyncio
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from app.core.config import settings
from app.services.data.eod_incremental import _get_calendar
from app.services.data.macro_sync import broadcast_to_all_stocks

logger = logging.getLogger(__name__)

# 外盘指数配置：field -> (显示名, 拉取函数)
_EXTERNAL_INDICES = {
    "us_sp500": ("标普500", lambda: _fetch_us_index(".INX")),
    "us_nasdaq": ("纳斯达克", lambda: _fetch_us_index(".IXIC")),
    "us_dow": ("道琼斯", lambda: _fetch_us_index(".DJI")),
    "hk_hsi": ("恒生指数", lambda: _fetch_hk_index("HSI")),
}

# 广播写入的 bin 字段名
EXTERNAL_FIELDS = [f"{k}_ret" for k in _EXTERNAL_INDICES]


def _state_file() -> Path:
    """最近一次外盘同步结果缓存（供 GET 查询，避免每次实时拉 akshare）。"""
    return Path(settings.PROJECT_ROOT) / "data" / "external_market.json"


def _fetch_us_index(symbol: str) -> pd.DataFrame:
    """拉美股指数日线，返回 date/close 两列（date 升序）。"""
    import akshare as ak

    df = ak.index_us_stock_sina(symbol=symbol)
    out = pd.DataFrame({
        "date": pd.to_datetime(df["date"]),
        "close": pd.to_numeric(df["close"], errors="coerce"),
    }).dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
    return out


def _fetch_hk_index(symbol: str) -> pd.DataFrame:
    """拉港股指数日线（东财接口列名 latest 即收盘），返回 date/close。"""
    import akshare as ak

    df = ak.stock_hk_index_daily_em(symbol=symbol)
    out = pd.DataFrame({
        "date": pd.to_datetime(df["date"]),
        "close": pd.to_numeric(df["latest"], errors="coerce"),
    }).dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
    return out


def _align_overnight_return(cal_dates: pd.DatetimeIndex, close: pd.Series) -> np.ndarray:
    """把外盘日收盘序列对齐到 A股日历的"开盘前最近一次外盘涨跌幅"。

    A股日 T 的值 = 最后一个外盘交易日 u（u 严格 < T）的 close-to-close 涨跌幅。
    用 searchsorted 取严格小于 T 的最近外盘日；外盘休市时该值保持（无新信息）。
    """
    if close.empty:
        return np.full(len(cal_dates), np.nan, dtype=np.float32)
    close = close.dropna().sort_index()
    if len(close) < 2:
        return np.full(len(cal_dates), np.nan, dtype=np.float32)
    ret = close.pct_change().values  # ret[i] = close[i]/close[i-1] - 1
    us_dates = close.index.values  # datetime64[ns], 升序
    idx = np.searchsorted(us_dates, cal_dates.values, side="left") - 1
    vals = np.full(len(cal_dates), np.nan, dtype=np.float32)
    valid = idx >= 0
    if valid.any():
        vals[valid] = ret[idx[valid]].astype(np.float32)
    return vals


def _latest_info(df: pd.DataFrame) -> dict:
    """从日线 DataFrame（date 升序）取最新交易日与涨跌幅，供 UI 展示。"""
    if df.empty:
        return {"last_date": None, "close": None, "ret": None}
    ret = df["close"].pct_change().iloc[-1]
    return {
        "last_date": str(df["date"].iloc[-1].date()),
        "close": round(float(df["close"].iloc[-1]), 2),
        "ret": round(float(ret), 4) if pd.notna(ret) else None,
    }


async def sync_external_market(provider_uri: str = None) -> dict:
    """拉取全部外盘指数 → 对齐 → 广播到 bin，并缓存最新值。

    Returns:
        dict: {synced_at, items: {field: {label, ok, last_date, close, ret, error}}}
    """
    provider_uri = provider_uri or settings.qlib_provider_path
    calendar = _get_calendar(provider_uri)
    if not calendar:
        return {"synced_at": None, "error": "A股日历为空（qlib 数据未同步），无法对齐外盘因子"}

    cal_dates = pd.to_datetime(calendar)
    items = {}
    raw_series = {}
    for field, (label, fetch) in _EXTERNAL_INDICES.items():
        try:
            df = await asyncio.to_thread(fetch)
            close = df.set_index("date")["close"]
            values = _align_overnight_return(cal_dates, close)
            # 广播是纯文件 IO，放线程池避免阻塞事件循环
            n = await asyncio.to_thread(broadcast_to_all_stocks, provider_uri, f"{field}_ret", values)
            items[field] = {
                "label": label, "ok": True,
                **_latest_info(df),
                "stocks_written": n,
            }
            # 缓存原始收盘序列，供回填等日历变化后 rebroadcast 重新对齐
            raw_series[field] = {
                "dates": [str(d.date()) for d in close.index],
                "closes": [None if pd.isna(v) else float(v) for v in close.values],
            }
            logger.info("外盘因子 %s(%s) 广播完成: %d 只股票, 最新 %s",
                        field, label, n, items[field]["last_date"])
        except Exception as e:  # noqa: BLE001
            items[field] = {"label": label, "ok": False, "error": str(e)[:200]}
            logger.warning("外盘因子 %s(%s) 拉取失败: %s", field, label, e)

    payload = {
        "synced_at": __import__("datetime").datetime.now().isoformat(),
        "items": items,
        "raw": raw_series,
    }
    try:
        _state_file().parent.mkdir(parents=True, exist_ok=True)
        _state_file().write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    except OSError as e:
        logger.warning("写入外盘状态缓存失败: %s", e)
    return payload


def rebroadcast_external_market(provider_uri: str = None) -> dict:
    """用缓存的原始外盘序列，按当前日历重新对齐并广播。

    供数据回填/修复完成后调用：回填会扩展 day.txt 与 OHLCV bin，若外盘因子
    是回填中广播的（对齐到中间态日历），这里按最终日历重新广播，避免长度异常。
    无缓存或日历为空时静默跳过。
    """
    provider_uri = provider_uri or settings.qlib_provider_path
    state = get_external_market_state()
    raw = state.get("raw") or {}
    if not raw:
        return {"rebroadcasted": 0, "reason": "no_cache"}
    calendar = _get_calendar(provider_uri)
    if not calendar:
        return {"rebroadcasted": 0, "reason": "empty_calendar"}
    cal_dates = pd.to_datetime(calendar)
    done = 0
    for field, r in raw.items():
        if not r.get("dates"):
            continue
        close = pd.Series(r["closes"], index=pd.to_datetime(r["dates"]), dtype="float64")
        values = _align_overnight_return(cal_dates, close)
        n = broadcast_to_all_stocks(provider_uri, f"{field}_ret", values)
        done += 1
        logger.info("外盘因子 %s 重新对齐广播: %d 只股票（日历 %d 天）", field, n, len(calendar))
    # 刷新 items 里的 last_date 等展示信息（日期不变，仅重广播，无需改 synced_at）
    return {"rebroadcasted": done, "calendar_days": len(calendar)}


def get_external_market_state() -> dict:
    """读取最近一次同步结果（无则返回空结构）。"""
    try:
        if _state_file().exists():
            return json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("读取外盘状态缓存失败: %s", e)
    return {"synced_at": None, "items": {}}
