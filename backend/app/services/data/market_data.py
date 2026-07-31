"""市值数据获取（含重试机制）"""
import logging
import time
import numpy as np
import pandas as pd

from app.services.data.code_utils import to_qlib_code

logger = logging.getLogger(__name__)

_market_cap_cache = None
_market_cap_cache_time = None

_MAX_RETRIES = 3
_RETRY_DELAY = 2  # seconds


def _fetch_via_spot_em() -> pd.DataFrame:
    """主数据源：东财 A 股实时行情（含市值字段）"""
    import akshare as ak
    df = ak.stock_zh_a_spot_em()
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={
        "代码": "code",
        "总市值": "total_mv",
        "流通市值": "circ_mv",
    })
    df["code"] = df["code"].apply(to_qlib_code)
    df = df.set_index("code")[["total_mv", "circ_mv"]]
    return df.dropna(subset=["total_mv"])


def fetch_market_cap_data() -> pd.DataFrame:
    """获取全 A 股市值数据（含重试 + 降级）

    优先使用 stock_zh_a_spot_em，失败重试 3 次（递增延迟）。

    Returns:
        DataFrame: index=股票代码(sh600000格式), columns=['total_mv', 'circ_mv']
    """
    global _market_cap_cache, _market_cap_cache_time

    if _market_cap_cache is not None and _market_cap_cache_time:
        age = time.time() - _market_cap_cache_time
        if age < 3600:  # 1 hour cache
            return _market_cap_cache

    last_error = None
    for attempt in range(_MAX_RETRIES):
        try:
            df = _fetch_via_spot_em()
            if not df.empty:
                _market_cap_cache = df
                _market_cap_cache_time = time.time()
                logger.info("市值数据获取成功(第%d次): %d 只股票", attempt + 1, len(df))
                return df
            else:
                logger.warning("市值数据为空(第%d次)", attempt + 1)
        except Exception as e:
            last_error = e
            logger.warning("市值数据获取失败(第%d次): %s", attempt + 1, e)
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_DELAY * (attempt + 1))  # incremental delay

    logger.error("市值数据获取失败(已重试%d次): %s", _MAX_RETRIES, last_error)
    return pd.DataFrame()


def get_log_market_cap() -> pd.Series:
    """获取对数市值"""
    df = fetch_market_cap_data()
    if df.empty:
        return pd.Series(dtype=float)
    return np.log(df["total_mv"].astype(float))
