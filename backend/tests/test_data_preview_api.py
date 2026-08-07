# -*- coding: utf-8 -*-
"""data_ext / preview 等 API 的 NaN 处理回归测试。

覆盖：
- data_preview_api 遇到 NaN 日历日（停牌/数据未发布）不崩溃、不返回 NaN：
  volume 等字段转 null，而不是 int(NaN) 抛 ValueError。
"""
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from app.api.data_ext import data_preview_api


def _df_with_nan_preview():
    """模拟 D.features 返回：最新行 OHLCV 全是 NaN（停牌/未发布）。"""
    idx = pd.MultiIndex.from_tuples(
        [
            ("SH600000", pd.Timestamp("2026-08-05")),
            ("SH600000", pd.Timestamp("2026-08-06")),
            ("SH600000", pd.Timestamp("2026-08-07")),
        ],
        names=["instrument", "datetime"],
    )
    return pd.DataFrame({
        "$open": [9.51, 9.28, np.nan],
        "$close": [9.63, 9.29, np.nan],
        "$high": [9.65, 9.35, np.nan],
        "$low": [9.45, 9.16, np.nan],
        "$volume": [89862760.0, 67232904.0, np.nan],
    }, index=idx)


@pytest.fixture
def mock_preview_D():
    mock = MagicMock()
    mock.features.return_value = _df_with_nan_preview()
    with patch("qlib.data.D", mock):
        yield mock


@patch("app.services.quant.qlib_init.is_qlib_available", return_value=True)
@patch("app.services.quant.qlib_init.init_qlib", return_value=True)
@pytest.mark.asyncio
async def test_data_preview_api_handles_nan(mock_init, mock_avail, mock_preview_D):
    """预览接口对 NaN 行不崩溃：volume/OHLC 转 null，不抛 int(NaN) ValueError。"""
    res = await data_preview_api(code="SH600000", limit=10)
    assert res.ok
    items = res.data["items"]
    assert items  # 至少返回数据
    for it in items:
        # volume 不允许 NaN（曾是 int(NaN) 崩溃点），应为 int 或 None
        assert it["volume"] is None or isinstance(it["volume"], int)
        # OHLC 应为 float 或 None
        assert it["close"] is None or isinstance(it["close"], float)
