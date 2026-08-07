# -*- coding: utf-8 -*-
"""rule_backtest 交易提取回归测试。

覆盖：
- _run_single_vbt 从 vbt orders.records_readable 正确区分 BUY/SELL：
  方向在 Side 列（Buy/Sell），Size 恒为正——此前误用 Size 正负判断导致
  所有成交都显示为 BUY（"结果很奇怪，压根没有卖出操作"）。
"""
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from app.services.quant.rule_backtest import _run_single_vbt


def _fake_records():
    """模拟 vbt pf.orders.records_readable：Size 恒为正，方向在 Side 列。"""
    return pd.DataFrame([
        {"Order Id": 0, "Column": "sh600000", "Timestamp": pd.Timestamp("2024-11-11"),
         "Size": 0.102881, "Price": 9.72, "Fees": 0.0013, "Side": "Buy"},
        {"Order Id": 1, "Column": "sh600000", "Timestamp": pd.Timestamp("2024-12-04"),
         "Size": 0.102881, "Price": 9.75, "Fees": 0.0013, "Side": "Sell"},
        {"Order Id": 2, "Column": "sh600000", "Timestamp": pd.Timestamp("2025-04-07"),
         "Size": 0.103627, "Price": 9.65, "Fees": 0.0013, "Side": "Buy"},
    ])


def test_run_single_vbt_extracts_buy_sell_direction():
    """方向必须取自 Side 列：Buy→BUY，Sell→SELL，不能全判成 BUY。"""
    close = pd.Series([10.0, 10.1, 10.2, 9.9, 10.0],
                      index=pd.date_range("2024-11-08", periods=5))
    fake_pf = MagicMock()
    fake_pf.orders.records_readable = _fake_records()
    fake_pf.returns.return_value = pd.Series([0.0, 0.01, -0.005, 0.002],
                                             index=pd.date_range("2024-11-12", periods=4))

    with patch("vectorbt.Portfolio.from_signals", return_value=fake_pf):
        returns, trades = _run_single_vbt(close, None, None, 0.0013, "sh600000", 10000)

    actions = [t["action"] for t in trades]
    assert actions == ["BUY", "SELL", "BUY"]  # 方向正确配对
    # 手续费保留 4 位（此前 round(…,2) 把 0.0013 显示成 0.0）
    assert trades[0]["cost"] == pytest.approx(0.0013, abs=1e-4)
    assert trades[0]["quantity"] == pytest.approx(0.102881, abs=1e-4)


def test_run_single_vbt_fallback_size_sign_when_no_side():
    """兼容旧版 vbt（无 Side 列）：回退用 Size 正负判断方向。"""
    close = pd.Series([10.0, 10.1, 10.2, 9.9, 10.0],
                      index=pd.date_range("2024-11-08", periods=5))
    rec = _fake_records().drop(columns=["Side"])
    rec.loc[0, "Size"] = 0.102881   # 正 → BUY
    rec.loc[1, "Size"] = -0.102881  # 负 → SELL
    fake_pf = MagicMock()
    fake_pf.orders.records_readable = rec
    fake_pf.returns.return_value = pd.Series([0.0, 0.01],
                                             index=pd.date_range("2024-11-12", periods=2))

    with patch("vectorbt.Portfolio.from_signals", return_value=fake_pf):
        _, trades = _run_single_vbt(close, None, None, 0.0013, "sh600000", 10000)

    # 3 行：第 3 行 Size 仍为正 → BUY
    assert [t["action"] for t in trades] == ["BUY", "SELL", "BUY"]
