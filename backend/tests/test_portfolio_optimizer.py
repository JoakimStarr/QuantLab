"""组合优化器测试：skfolio 后端（mean-variance / risk-parity / 等权回退）。"""
import numpy as np
import pandas as pd
import pytest

from app.services.quant.portfolio_optimizer import optimize_portfolio, _equal_weight


@pytest.fixture
def scores():
    rng = np.random.default_rng(4)
    return pd.Series(rng.normal(0, 1, 8),
                     index=[f"s{i}" for i in range(8)])


class TestSkfolioBackend:
    def test_mean_variance_auto(self, scores):
        w = optimize_portfolio(scores, method="mean_variance", backend="auto")
        assert isinstance(w, pd.Series)
        assert set(w.index) == set(scores.index)
        assert abs(w.sum() - 1.0) < 1e-6
        assert (w >= 0).all()

    def test_max_sharpe(self, scores):
        w = optimize_portfolio(scores, method="max_sharpe", backend="skfolio")
        assert abs(w.sum() - 1.0) < 1e-6

    def test_min_volatility(self, scores):
        w = optimize_portfolio(scores, method="min_volatility", backend="skfolio")
        assert abs(w.sum() - 1.0) < 1e-6

    def test_risk_parity(self, scores):
        w = optimize_portfolio(scores, method="risk_parity")
        assert abs(w.sum() - 1.0) < 1e-6
        # 风险平价权重分布较均匀
        assert w.std() < 0.1

    def test_max_weight_respected(self, scores):
        w = optimize_portfolio(scores, method="min_volatility", backend="skfolio",
                               max_weight=0.25)
        assert w.max() <= 0.25 + 1e-6

    def test_industry_constraint_ignored_gracefully(self, scores):
        """行业映射传入 skfolio 不报错（当前实现不强制行业约束，文档化）。"""
        ind_map = {f"s{i}": "ind_a" if i < 4 else "ind_b" for i in range(8)}
        w = optimize_portfolio(scores, method="mean_variance", backend="skfolio",
                               industry_map=ind_map, max_industry_exposure=0.5)
        assert abs(w.sum() - 1.0) < 1e-6

    def test_empty_scores(self):
        w = optimize_portfolio(pd.Series(dtype=float), method="mean_variance")
        assert w.empty

    def test_skfolio_failure_falls_back_to_equal_weight(self, scores, monkeypatch):
        """skfolio 内部失败 → 回退等权（结果仍有效）。"""
        monkeypatch.setattr(
            "app.services.quant.portfolio_optimizer._optimize_skfolio",
            lambda *a, **k: None,
        )
        w = optimize_portfolio(scores, method="mean_variance", backend="skfolio")
        assert abs(w.sum() - 1.0) < 1e-6
        assert (w >= 0).all()

    def test_backend_pypfopt_degraded_to_skfolio(self, scores):
        """backend="pypfopt"（历史参数）现在走 skfolio，不再崩溃。"""
        w = optimize_portfolio(scores, method="mean_variance", backend="pypfopt")
        assert abs(w.sum() - 1.0) < 1e-6


class TestEqualWeight:
    def test_basic(self):
        w = _equal_weight(pd.Series([1, 2, 3, 4]))
        assert (w == 0.25).all()

    def test_empty(self):
        assert _equal_weight(pd.Series(dtype=float)).empty
