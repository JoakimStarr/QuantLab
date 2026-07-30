"""市场行情 API：多指数 K 线与市场概览。

通过 qlib 读取指数 OHLCV 数据，支持日线/周线/月线 K 线，以及多指数实时行情概览。
"""
import asyncio
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Query

from app.core.config import settings
from app.core.errors import AppError
from app.schemas.common import ApiResponse
from app.services.quant.qlib_init import is_qlib_available, init_qlib

router = APIRouter(prefix="/market", tags=["market"])
logger = logging.getLogger(__name__)

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
    limit: int = Query(120, description="返回数据条数上限", ge=1, le=500),
):
    """获取指数K线数据"""
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)

    idx = SUPPORTED_INDICES.get(index_code.upper())
    if not idx:
        return ApiResponse(ok=False, error={
            "code": "UNSUPPORTED_INDEX",
            "message": f"Unsupported index: {index_code}",
            "status": 400,
        })

    qlib_code = idx["code"]

    # 计算日期范围
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")
    if not start_date:
        days = limit * 2 if period == "1d" else limit * 7
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    def _load():
        init_qlib()
        from qlib.data import D
        import pandas as pd

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

        # 按周期聚合
        if period == "1w":
            df = _resample_kline(df, "W")
        elif period == "1M":
            df = _resample_kline(df, "ME")

        # 限制返回条数
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
        loop = asyncio.get_running_loop()
        items = await loop.run_in_executor(None, _load)
        return ApiResponse(ok=True, data={
            "index_code": index_code,
            "index_name": idx["name"],
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

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")

    def _load():
        init_qlib()
        from qlib.data import D

        d = chr(36)
        close_field = d + "close"
        items = []
        for code, info in SUPPORTED_INDICES.items():
            try:
                df = D.features(
                    [info["code"]], [close_field],
                    start_time=start_date, end_time=end_date, freq="day",
                )
                if df is not None and not df.empty:
                    df = df.reset_index()
                    closes = df[close_field].values
                    if len(closes) >= 2:
                        latest = float(closes[-1])
                        prev = float(closes[-2])
                        pct = (latest - prev) / prev * 100
                    elif len(closes) == 1:
                        latest = float(closes[0])
                        pct = 0
                    else:
                        continue
                    items.append({
                        "code": code,
                        "name": info["name"],
                        "price": round(latest, 4),
                        "pct_change": round(pct, 2),
                    })
            except Exception as e:
                logger.debug("获取 %s 行情失败: %s", code, e)
                continue
        return items

    try:
        loop = asyncio.get_running_loop()
        items = await loop.run_in_executor(None, _load)
        return ApiResponse(ok=True, data={"items": items})
    except Exception as e:
        logger.error("市场概览失败: %s", e)
        return ApiResponse(ok=False, error={
            "code": "OVERVIEW_ERROR",
            "message": str(e),
            "status": 500,
        })
