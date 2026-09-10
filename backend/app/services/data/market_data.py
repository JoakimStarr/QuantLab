"""市值数据获取 —— 已弃用（A4）。

原实现取 akshare **实时快照**市值，被中性化套用到全部历史截面 → 前视偏差。
现改为 PIT 市值：``app/services/factor/market_cap_pit.py``（pe_ttm × 按公告日
前向填充的净利润）。此处保留函数名以兼容旧引用，但**显式报错**，避免任何路径
静默回退到实时快照。
"""
import logging

import pandas as pd

logger = logging.getLogger(__name__)

_DEPRECATION_MSG = (
    "实时快照市值路径已弃用（A4 前视偏差修复），请改用 PIT 市值："
    "app.services.factor.market_cap_pit.load_pit_log_market_cap"
)


def fetch_market_cap_data() -> pd.DataFrame:
    """已弃用：实时快照市值会引入前视偏差，禁止使用。"""
    raise RuntimeError(_DEPRECATION_MSG)


def get_log_market_cap() -> pd.Series:
    """已弃用：实时快照对数市值会引入前视偏差，禁止使用。"""
    raise RuntimeError(_DEPRECATION_MSG)
