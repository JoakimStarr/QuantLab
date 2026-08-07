# -*- coding: utf-8 -*-
"""market API 单元测试。

覆盖：
- get_index_kline 过滤 NaN 日历日（"今天"数据未发布时 qlib 返回 NaN 行，
  不应把 NaN 当作最新行情/指标）
- market_overview 忽略 NaN 收盘价（price/pct 不返回 null）
"""
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from app.api.market import get_index_kline, market_overview


def _df_with_nan_tail():
    """模拟 D.features 返回：含 NaN 日历日（如今天 08-07 无数据）。"""
    idx = pd.MultiIndex.from_product(
        [["sh000300"], pd.to_datetime(["2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"])],
        names=["instrument", "datetime"],
    )
    df = pd.DataFrame({
        "$open": [4566.0, 4550.0, 4621.0, np.nan],
        "$high": [4613.0, 4679.0, 4675.0, np.nan],
        "$low": [4555.0, 4550.0, 4611.0, np.nan],
        "$close": [4600.0, 4658.0, 4651.0, np.nan],
        "$volume": [24207362048, 27692191744, 24751073280, np.nan],
    }, index=idx)
    return df


@pytest.fixture
def mock_qlib_D():
    """把 qlib.data.D 替换为带 features() 的 MagicMock。"""
    mock = MagicMock()
    mock.features.return_value = _df_with_nan_tail()
    with patch("qlib.data.D", mock):
        yield mock


@patch("app.api.market.is_qlib_available", return_value=True)
@patch("app.api.market.init_qlib", return_value=True)
@pytest.mark.asyncio
async def test_get_index_kline_filters_nan_tail(mock_init, mock_avail, mock_qlib_D):
    """K线接口过滤 NaN 日历日：08-07（无数据）不应出现在 items 里。"""
    res = await get_index_kline("SH000300", limit=10)
    assert res.ok
    dates = [it["date"] for it in res.data["items"]]
    assert "2026-08-07" not in dates
    assert "2026-08-06" in dates
    # 所有行都有真实 close
    assert all(it["close"] is not None for it in res.data["items"])


@patch("app.api.market.is_qlib_available", return_value=True)
@patch("app.api.market.init_qlib", return_value=True)
@pytest.mark.asyncio
async def test_market_overview_ignores_nan_close(mock_init, mock_avail, mock_qlib_D):
    """概览接口忽略 NaN 收盘价：price/pct 不返回 null。"""
    res = await market_overview()
    assert res.ok
    assert res.data["items"]  # 至少一个指数有真实价
    for it in res.data["items"]:
        assert it["price"] is not None
        assert it["pct_change"] is not None
