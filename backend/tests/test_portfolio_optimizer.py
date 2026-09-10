"""组合优化器纯逻辑测试 + vbt optimize 权重接线测试。

验证 optimize_portfolio：
- 权重和为 1
- 单股上限约束
- 权重与 score 排序一致（高分股权重更高）
- 约束不可行 / 空输入的回退行为

以及 run_vbt_backtest(portfolio_method="optimize") 的权重 sizing 与如实披露。
"""

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from scipy.stats import spearmanr

from app.services.quant.portfolio_optimizer import optimize_portfolio
from app.services.quant.vbt_backtest import run_vbt_backtest


def _make_scores(n=30, seed=42):
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(0, 1, size=n), index=[f"s{i}" for i in range(n)])


class TestOptimizePortfolio:
    """optimize_portfolio 纯逻辑测试。"""

    def test_weights_sum_to_one(self):
        w = optimize_portfolio(_make_scores(), max_weight=0.2)
        assert len(w) == 30
        assert w.sum() == pytest.approx(1.0, abs=1e-8)
        assert (w >= 0).all()

    def test_max_weight_cap_respected(self):
        w = optimize_portfolio(_make_scores(), max_weight=0.1)
        assert w.max() <= 0.1 + 1e-6

    def test_score_ordering_consistency(self):
        """高分股应获得更高权重（均值-方差 + 单位协方差下单调）。

        均值-方差会把低分股权重压到 0（大量并列 0 会拉低全样本秩相关），
        因此在持仓子集上验证排序一致性，并验证高分段拿走更多总权重。
        """
        scores = _make_scores(seed=7)
        w = optimize_portfolio(scores, max_weight=0.2)
        pos = w[w > 0]
        assert len(pos) >= 2, f"正权重持仓过少: {pos.to_dict()}"
        rho, _ = spearmanr(scores[pos.index].values, pos.values)
        assert rho > 0.8, f"持仓权重与分数秩相关过低: {rho}"
        top10 = scores.nlargest(10).index
        bot10 = scores.nsmallest(10).index
        assert w[top10].sum() > w[bot10].sum(), "高分段总权重应大于低分段"

    def test_empty_scores_returns_empty(self):
        w = optimize_portfolio(pd.Series(dtype=float))
        assert isinstance(w, pd.Series)
        assert w.empty

    def test_infeasible_cap_falls_back_equal_weight(self):
        """n=3、max_weight=0.1 不可行（1/3 > 0.1）：回退等权并保持和为 1。"""
        w = optimize_portfolio(_make_scores(n=3), max_weight=0.1)
        assert w.sum() == pytest.approx(1.0, abs=1e-8)
        assert np.allclose(w.values, 1.0 / 3.0)

    def test_tight_cap_concentrates_less_than_loose(self):
        """上限越紧，权重分布越接近等权（基尼/最大最小比更小）。"""
        scores = _make_scores(seed=11)
        w_tight = optimize_portfolio(scores, max_weight=0.06)
        w_loose = optimize_portfolio(scores, max_weight=0.5)
        assert w_tight.max() / max(w_tight.min(), 1e-12) <= w_loose.max() / max(w_loose.min(), 1e-12)


_DATES = pd.date_range("2024-01-02", periods=4, freq="B")


def _make_signal_multi():
    """4 只股票 × 4 日信号，分数有明显梯度。"""
    tuples = []
    vals = []
    for d in _DATES:
        for i, code in enumerate(["A", "B", "C", "D"]):
            tuples.append((d, code))
            vals.append(10.0 - i * 2.0)  # A=10 > B=8 > C=6 > D=4
    return pd.DataFrame({"score": vals}, index=pd.MultiIndex.from_tuples(tuples, names=["datetime", "instrument"]))


def _make_prices_multi():
    return pd.DataFrame(
        {
            "A": [10.0, 11.0, 12.0, 13.0],
            "B": [10.0, 10.5, 11.0, 11.5],
            "C": [10.0, 9.5, 10.0, 10.5],
            "D": [10.0, 10.2, 10.4, 10.6],
        },
        index=_DATES,
    )


class TestVbtOptimizeWiring:
    """run_vbt_backtest(portfolio_method="optimize") 接线测试。"""

    def _run(self, **kwargs):
        with (
            patch("app.services.quant.vbt_backtest._load_prices", return_value=_make_prices_multi()),
            patch("app.services.quant.qlib_init.init_qlib"),
        ):
            return run_vbt_backtest(
                _make_signal_multi(),
                start="2024-01-02",
                end="2024-01-05",
                topk=3,
                n_drop=0,
                rebalance_freq="day",
                benchmark=None,
                slippage_bps=0,
                **kwargs,
            )

    def test_optimize_reports_actual_method(self):
        result = self._run(portfolio_method="optimize")
        assert result["portfolio_method"] == "optimize"
        assert len(result["returns"]) > 0

    def test_optimize_buys_use_weight_sizing(self):
        """optimize 下买单金额 = 目标权重 × 初始资金，而非等权 init_cash/topk。"""
        result = self._run(portfolio_method="optimize", capital=1_000_000)
        buys = [t for t in result["trades"] if t["action"] == "BUY"]
        assert buys, "应产生买入交易"
        totals = {t["code"]: t["total"] for t in buys}
        # 等权基准：1_000_000/3 ≈ 333_333；优化权重应偏离等权
        assert any(abs(v - 1_000_000 / 3) > 1.0 for v in totals.values()), f"买单金额仍是等权: {totals}"

    def test_default_stays_topk_dropout(self):
        """默认（不传 portfolio_method）保持 topk_dropout 等权。"""
        result = self._run(capital=900_000)
        assert result["portfolio_method"] == "topk_dropout"
        buys = [t for t in result["trades"] if t["action"] == "BUY"]
        assert buys
        for t in buys:
            assert t["total"] == pytest.approx(900_000 / 3, rel=0.01)

    def test_optimize_fallback_reported_honestly(self):
        """优化器不可用时不得假装 optimize 生效：如实报告 topk_dropout。"""
        with (
            patch("app.services.quant.vbt_backtest._load_prices", return_value=_make_prices_multi()),
            patch("app.services.quant.qlib_init.init_qlib"),
            patch.dict("sys.modules", {"app.services.quant.portfolio_optimizer": None}),
        ):
            result = run_vbt_backtest(
                _make_signal_multi(),
                start="2024-01-02",
                end="2024-01-05",
                topk=3,
                n_drop=0,
                rebalance_freq="day",
                benchmark=None,
                slippage_bps=0,
                portfolio_method="optimize",
            )
        assert result["portfolio_method"] == "topk_dropout"
