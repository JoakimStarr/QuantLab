"""市场行情 API：多指数 K 线与市场概览。

通过 qlib 读取指数 OHLCV 数据，支持日线/周线/月线 K 线，以及多指数实时行情概览。
"""
import logging
import re
from datetime import datetime, timedelta

import numpy as np
from fastapi import APIRouter, Query

from app.core.cache import TTLCache
from app.core.errors import AppError
from app.core.executor import run_io_cpu
from app.schemas.common import ApiResponse
from app.services.quant.qlib_init import init_qlib, is_qlib_available

router = APIRouter(prefix="/market", tags=["market"])
logger = logging.getLogger(__name__)

# 行情接口缓存：日级 K 线数据在日内是静态的，Dashboard 轮询 / 页面切换时
# 反复读 qlib bin 是纯浪费。overview 20s / kline 60s 的 TTL 足够轮询场景，
# 且同步落新数据后 TTL 内最多短暂旧值（涨跌展示场景可接受）。
_overview_cache = TTLCache(ttl=20)
_kline_cache = TTLCache(ttl=60, maxsize=128)

# 支持的指数列表
SUPPORTED_INDICES = {
    "SH000300": {"name": "沪深300", "code": "sh000300", "desc": "CSI 300"},
    "SH000016": {"name": "上证50", "code": "sh000016", "desc": "SSE 50"},
    "SH000905": {"name": "中证500", "code": "sh000905", "desc": "CSI 500"},
    "SH000852": {"name": "中证1000", "code": "sh000852", "desc": "CSI 1000"},
    "SZ399001": {"name": "深证成指", "code": "sz399001", "desc": "SZSE Component"},
    "SZ399006": {"name": "创业板指", "code": "sz399006", "desc": "ChiNext"},
    "SH000688": {"name": "科创50", "code": "sh000688", "desc": "STAR 50"},
    "SH000001": {"name": "上证指数", "code": "sh000001", "desc": "SSE Composite"},
}


def _quote_from_closes(closes: np.ndarray) -> dict | None:
    """由收盘价序列算最新价/涨跌幅（overview 用）。

    过滤 NaN（如"今天"数据未发布时 qlib 返回 NaN 日历日），只用真实收盘价，
    避免 price/pct 变成 null 或 NaN。
    """
    closes = closes[~np.isnan(closes.astype(float))]
    if len(closes) >= 2:
        latest = float(closes[-1])
        prev = float(closes[-2])
        pct = (latest - prev) / prev * 100
        return {"price": round(latest, 4), "pct_change": round(pct, 2)}
    if len(closes) == 1:
        return {"price": round(float(closes[0]), 4), "pct_change": 0}
    return None


@router.get("/indices")
async def list_indices():
    """列出支持的指数"""
    return ApiResponse(ok=True, data={
        "items": [
            {"code": k, "name": v["name"], "desc": v["desc"], "qlib_code": v["code"]}
            for k, v in SUPPORTED_INDICES.items()
        ]
    })


@router.get("/kline/{index_code}")
async def get_index_kline(
    index_code: str,
    period: str = Query("1d", description="K线周期: 1d/1w/1M"),
    start_date: str = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: str = Query(None, description="结束日期 YYYY-MM-DD"),
    limit: int = Query(120, description="返回数据条数上限（仅未指定日期区间时生效）", ge=1, le=10000),
):
    """获取指数/个股K线数据。

    当调用方显式指定 start_date+end_date 时返回区间内全部数据（用于回测买卖点
    全量叠加，防止多年区间被 limit 截断）；仅用 limit 兜底限制超大区间。
    """
    """获取指数/个股K线数据"""
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)

    idx = SUPPORTED_INDICES.get(index_code.upper())
    if idx:
        qlib_code = idx["code"]
        name = idx["name"]
    else:
        # 个股：支持 SH600000 / SZ000001 / BJ430047 前缀，或 6 位无前缀代码（如 001337）
        low = index_code.lower()
        if re.fullmatch(r"(sh|sz|bj)\d{6}", low):
            qlib_code = low
            name = index_code.upper()
        elif re.fullmatch(r"\d{6}", low):
            from app.services.data.code_utils import to_qlib_code

            qlib_code = to_qlib_code(low)
            name = index_code.upper()
        else:
            return ApiResponse(ok=False, error={
                "code": "UNSUPPORTED_INDEX",
                "message": f"Unsupported index/stock: {index_code}",
                "status": 400,
            })

    # 计算日期范围
    # 显式指定日期区间时返回区间内全部数据（供回测叠加买卖点），
    # 否则按 limit 兜底截断（默认模式只取最近 N 条）。
    has_explicit_range = bool(start_date) or bool(end_date)
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")
    if not start_date:
        days = limit * 2 if period == "1d" else limit * 7
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    # K 线数据日内静态：按 (标的, 周期, 区间, limit) 做 60s 缓存，切页/轮询不再读 bin。
    # 显式区间（回测买卖点叠加）请求的是历史静态数据，缓存同样有效。
    cache_key = (qlib_code, period, start_date, end_date, limit)
    cached = _kline_cache.get(cache_key)
    if cached is not None:
        return ApiResponse(ok=True, data={
            "index_code": index_code,
            "index_name": name,
            "period": period,
            "count": len(cached),
            "items": cached,
        })

    def _load():
        init_qlib()
        from qlib.data import D

        d = chr(36)
        fields = [d + "open", d + "high", d + "low", d + "close", d + "volume"]
        df = D.features(
            [qlib_code], fields,
            start_time=start_date, end_time=end_date, freq="day",
        )

        if df is None or df.empty:
            return []

        # 重置索引（MultiIndex: instrument, datetime）
        df = df.reset_index()
        df = df.rename(columns={
            "instrument": "code",
            "datetime": "date",
            d + "open": "open",
            d + "high": "high",
            d + "low": "low",
            d + "close": "close",
            d + "volume": "volume",
        })

        # 过滤无实际数据的日历日（如"今天"数据未发布时 qlib 返回 NaN 行）：
        # D.features 对 day.txt 内每个日历日都会返回一行，即使 bin 是 NaN。
        # 直接按 close 非 NaN 过滤，避免前端把 NaN 当成最新行情/指标算成 NaN。
        import numpy as np
        df = df[df["close"].notna() & np.isfinite(df["close"].astype(float))]

        # 按周期聚合
        if period == "1w":
            df = _resample_kline(df, "W")
        elif period == "1M":
            df = _resample_kline(df, "ME")

        # 限制返回条数（仅默认模式未显式指定区间时生效）
        if not has_explicit_range:
            df = df.tail(limit)

        # 计算涨跌幅
        df["pct_change"] = df["close"].pct_change() * 100

        items = []
        for _, row in df.iterrows():
            items.append({
                "date": row["date"].strftime("%Y-%m-%d") if hasattr(row["date"], "strftime") else str(row["date"]),
                "open": round(float(row["open"]), 4),
                "high": round(float(row["high"]), 4),
                "low": round(float(row["low"]), 4),
                "close": round(float(row["close"]), 4),
                "volume": int(row["volume"]) if row["volume"] == row["volume"] else 0,
                "pct_change": round(float(row["pct_change"]), 2) if row["pct_change"] == row["pct_change"] else 0,
            })
        return items

    try:
        items = await run_io_cpu(_load)
        _kline_cache.set(cache_key, items)
        return ApiResponse(ok=True, data={
            "index_code": index_code,
            "index_name": name,
            "period": period,
            "count": len(items),
            "items": items,
        })
    except Exception as e:
        logger.error("获取指数K线失败 %s: %s", index_code, e)
        return ApiResponse(ok=False, error={
            "code": "KLINE_ERROR",
            "message": str(e),
            "status": 500,
        })


def _resample_kline(df, freq: str):
    """按周/月聚合K线（兼容 pandas 2.2+ 频率别名）"""
    import pandas as _pd
    # pandas 2.2+ removed legacy aliases: W->W, M->ME
    if freq == "M" and _pd.__version__ >= "2.2":
        freq = "ME"
    df = df.set_index("date")
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    df = df.resample(freq).agg(agg).dropna()
    return df.reset_index()


@router.get("/overview")
async def market_overview():
    """获取市场概览（多指数最新行情）"""
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)

    # Dashboard 轮询场景：20s TTL 缓存，避免每 1~3s 一次轮询就 8 次读 qlib bin
    cached = _overview_cache.get("overview")
    if cached is not None:
        return ApiResponse(ok=True, data={"items": cached})

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")

    def _load():
        init_qlib()
        from qlib.data import D

        d = chr(36)
        close_field = d + "close"
        order = list(SUPPORTED_INDICES.items())
        # 批量一次读全部指数 close（一次 D.features 调用），失败时逐指数兜底
        items = []
        try:
            df = D.features(
                [info["code"] for _, info in order], [close_field],
                start_time=start_date, end_time=end_date, freq="day",
            )
            combined = df is not None and not df.empty
        except Exception as e:  # noqa: BLE001
            logger.debug("概览批量读取失败，逐指数兜底: %s", e)
            combined = False
        if combined:
            df = df.reset_index()
            for code, info in order:
                try:
                    sub = df.loc[df["instrument"] == info["code"], close_field]
                    quote = _quote_from_closes(sub.values)
                    if quote:
                        items.append({"code": code, "name": info["name"], **quote})
                except Exception as e:  # noqa: BLE001
                    logger.debug("获取 %s 行情失败: %s", code, e)
        else:
            # 兜底：逐指数独立读取（单 code 请求对缺失目录更宽容）
            for code, info in order:
                try:
                    df = D.features(
                        [info["code"]], [close_field],
                        start_time=start_date, end_time=end_date, freq="day",
                    )
                    if df is not None and not df.empty:
                        quote = _quote_from_closes(df[close_field].values)
                        if quote:
                            items.append({"code": code, "name": info["name"], **quote})
                except Exception as e:  # noqa: BLE001
                    logger.debug("获取 %s 行情失败: %s", code, e)
        return items

    try:
        items = await run_io_cpu(_load)
        _overview_cache.set("overview", items)
        return ApiResponse(ok=True, data={"items": items})
    except Exception as e:
        logger.error("市场概览失败: %s", e)
        return ApiResponse(ok=False, error={
            "code": "OVERVIEW_ERROR",
            "message": str(e),
            "status": 500,
        })
