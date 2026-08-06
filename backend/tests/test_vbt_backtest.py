# -*- coding: utf-8 -*-
"""vbt_backtest 时序回归测试。

验证 T+1 执行修复：
- 信号在 T 日收盘生成 → 成交必须发生在 T+1（同收盘价成交=前视，已修）
- 首个交易日无前一日信号 → 不产生交易
- 基准收益与策略收益按同日对齐（旧代码 shift(-1) 使基准提前一天）
"""
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from app.services.quant.vbt_backtest import run_vbt_backtest

_DATES = pd.date_range("2024-01-02", periods=4, freq="B")  # 01-02..01-05


def _make_signal():
    """信号：01-02 A 高分、01-03 A 低分（应卖出）、01-04/01-05 A 高分。"""
    idx = pd.MultiIndex.from_tuples(
        [(d, "A") for d in _DATES], names=["datetime", "instrument"]
    )
    return pd.DataFrame(
        {"score": [10.0, 1.0, 10.0, 10.0]}, index=idx
    )


def _make_prices():
    return pd.DataFrame({"A": [10.0, 11.0, 12.0, 13.0]}, index=_DATES)


def _run(signal):
    with patch("app.services.quant.vbt_backtest._load_prices", return_value=_make_prices()), \
         patch("app.services.quant.qlib_init.init_qlib"):
        return run_vbt_backtest(
            signal, start="2024-01-02", end="2024-01-05",
            topk=1, n_drop=0, rebalance_freq="day", benchmark=None,
            slippage_bps=0,  # 显式 0 滑点，保证成交价等于收盘价（测试关注 T+1 时序）
        )


def test_vbt_trade_happens_next_day():
    """T 日信号 → T+1 成交（无同收盘价前视）。"""
    result = _run(_make_signal())
    trades = result["trades"]
    buy_trades = [t for t in trades if t["action"] == "BUY"]
    assert buy_trades, "应产生买入交易"
    first_buy_date = buy_trades[0]["date"][:10]
    # 01-02 信号 → 01-03 成交（首个交易日无信号可用，不能 01-02 买）
    assert first_buy_date == "2024-01-03", f"首笔买入应在 T+1=01-03，实际 {first_buy_date}"
    assert buy_trades[0]["price"] == pytest.approx(11.0, abs=1e-3)


def test_vbt_first_day_no_trade():
    """首个交易日（无前一日信号）不产生交易。"""
    result = _run(_make_signal())
    assert not any(t["date"].startswith("2024-01-02") for t in result["trades"])


def test_vbt_returns_and_benchmark_aligned():
    """基准收益与策略收益同日对齐（修复 shift(-1) 导致的错位）。

    基准走 qlib D.features 真实数据，单测不便执行；用源码检查保证
    pct_change().shift(-1) 的错位写法不再出现。
    """
    import inspect

    from app.services.quant import vbt_backtest as module

    src = inspect.getsource(module)
    assert "pct_change().shift(-1)" not in src, "基准不再用 shift(-1) 提前一天"
    assert "bench_series.pct_change().dropna()" in src


def _run_etf(signal):
    with patch("app.services.quant.vbt_backtest._load_prices", return_value=_make_prices()), \
         patch("app.services.quant.qlib_init.init_qlib"):
        return run_vbt_backtest(
            signal, start="2024-01-02", end="2024-01-05",
            topk=1, n_drop=0, rebalance_freq="day", benchmark=None,
            slippage_bps=0, asset_class="etf",
        )


def test_vbt_etf_trades_on_signal_day():
    """ETF T+0 语义：信号日收盘成交（首个交易日 01-02 即可买入）。"""
    result = _run_etf(_make_signal())
    buy_trades = [t for t in result["trades"] if t["action"] == "BUY"]
    assert buy_trades, "ETF 分支应产生买入交易"
    # 与 stock（T+1=01-03）不同，ETF 在信号日 01-02 成交
    assert buy_trades[0]["date"].startswith("2024-01-02"), f"ETF 首笔买入应在信号日 01-02，实际 {buy_trades[0]['date']}"
