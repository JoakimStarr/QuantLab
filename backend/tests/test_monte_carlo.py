"""蒙特卡罗模拟（回测指标 bootstrap + 因子 IC 置换检验）单测（无需 DB）。"""
from unittest.mock import patch

import numpy as np
import pandas as pd

from app.services.quant.monte_carlo import (
    bootstrap_metric_ci,
    metric_values,
    permutation_ic_test,
)

_METRICS = ("sharpe", "sortino", "calmar", "cagr",
            "annual_volatility", "max_drawdown", "win_rate")


def _returns(n=200, seed=7):
    rng = np.random.default_rng(seed)
    return rng.normal(0.0005, 0.01, n)


def test_metric_values_structure():
    r = metric_values(_returns())
    for k in _METRICS:
        assert k in r, f"缺指标 {k}"
        assert r[k] is not None
    assert 0 <= r["win_rate"] <= 1


def test_bootstrap_structure_and_ci():
    r = bootstrap_metric_ci(_returns(), n_iter=200, block=20, seed=42)
    assert r["n_obs"] == 200
    assert r["n_iter"] == 200
    assert len(r["sharpe_samples"]) == 200
    for m in _METRICS:
        mm = r["metrics"][m]
        assert mm["lo"] <= mm["median"] <= mm["hi"], m
        assert mm["lo"] <= mm["hi"], m
        assert mm["std"] >= 0, m


def test_bootstrap_seed_reproducible():
    a = bootstrap_metric_ci(_returns(), n_iter=100, seed=42)
    b = bootstrap_metric_ci(_returns(), n_iter=100, seed=42)
    assert a["sharpe_samples"] == b["sharpe_samples"]


def test_bootstrap_too_short():
    r = bootstrap_metric_ci(np.random.randn(10))
    assert r["metrics"] == {}
    assert r["n_iter"] == 0
    assert "样本不足" in (r.get("error") or "")


def test_bootstrap_stable_metric_narrow_ci():
    """稳定正收益：胜率点估计高且 CI 收窄（不放宽到 1.0 以下）。"""
    rng = np.random.default_rng(3)
    ret = rng.normal(0.008, 0.005, 400)
    r = bootstrap_metric_ci(ret, n_iter=200, seed=1)
    wr = r["metrics"]["win_rate"]
    assert wr["point"] > 0.9
    assert wr["lo"] > 0.9


async def test_monte_carlo_endpoint_ok(monkeypatch):
    from app.api.strategy_ext import MonteCarloRequest, monte_carlo_api

    nav = [1.0, 1.01, 1.015, 0.99, 1.03, 1.05, 1.04, 1.07, 1.09, 1.11] * 40
    fake_result = {
        "id": 42,
        "strategy_id": 1,
        "start_date": "2020-01-01",
        "end_date": "2024-12-31",
        "nav_curve": {
            "dates": [f"2020-{i % 12 + 1:02d}-{(i % 28) + 1:02d}" for i in range(400)],
            "portfolio": nav,
        },
    }

    async def fake_get(result_id):
        return fake_result if result_id == 42 else None

    async def fake_run_cpu(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("app.api.strategy_ext.get_backtest_result", fake_get)
    monkeypatch.setattr("app.core.executor.run_cpu", fake_run_cpu)

    res = await monte_carlo_api(42, MonteCarloRequest(n_iter=50, block=10))
    assert res.ok is True
    data = res.data
    assert data["result_id"] == 42
    assert data["n_iter"] == 50
    assert data["block"] == 10
    # nav 400 点 → pct_change 后 399 个日收益
    assert data["n_obs"] == len(nav) - 1
    assert "sharpe" in data["metrics"]
    assert len(data["sharpe_samples"]) == 50


async def test_monte_carlo_endpoint_no_nav(monkeypatch):
    from app.api.strategy_ext import MonteCarloRequest, monte_carlo_api

    async def fake_get(result_id):
        return {"id": 1, "nav_curve": None}

    monkeypatch.setattr("app.api.strategy_ext.get_backtest_result", fake_get)
    res = await monte_carlo_api(1, MonteCarloRequest())
    assert res.ok is False
    assert res.error["code"] == "NO_NAV"


async def test_monte_carlo_endpoint_not_found(monkeypatch):
    from app.api.strategy_ext import MonteCarloRequest, monte_carlo_api

    async def fake_get(result_id):
        return None

    monkeypatch.setattr("app.api.strategy_ext.get_backtest_result", fake_get)
    res = await monte_carlo_api(999, MonteCarloRequest())
    assert res.ok is False
    assert res.error["code"] == "NOT_FOUND"


# ---------- 因子 IC 置换检验 ----------


def _make_factor_panel(strong: bool, seed=0, n_days=80, n_stocks=200):
    """构造 MultiIndex (datetime, instrument) 因子/标签面板。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n_days)
    insts = [f"sh{i:06d}" for i in range(n_stocks)]
    idx = pd.MultiIndex.from_product([dates, insts], names=["datetime", "instrument"])
    n = len(idx)
    label_vals = rng.normal(0.0003, 0.01, n)
    if strong:
        # 因子与 label 强正相关（截面内），保证真实 IC 明显偏离 0
        order = np.argsort(label_vals)
        factor = np.empty(n)
        factor[order] = np.linspace(-1, 1, n) + rng.normal(0, 0.5, n)
    else:
        factor = rng.normal(0, 1, n)
    factor_df = pd.DataFrame({"factor": factor}, index=idx)
    label_df = pd.DataFrame({"label": label_vals}, index=idx)
    return factor_df, label_df


def test_permutation_strong_factor_significant():
    f, lab = _make_factor_panel(strong=True)
    r = permutation_ic_test(f, lab, n_permutations=200, seed=42)
    assert r["n_permutations"] == 200
    assert r["ic_obs"] > 0.2
    assert r["p_value"] < 0.05
    assert r["significant"] is True
    # 零分布以 0 为中心
    assert abs(r["perm_mean"]) < 0.02


def test_permutation_random_factor_insignificant():
    f, lab = _make_factor_panel(strong=False)
    r = permutation_ic_test(f, lab, n_permutations=200, seed=42)
    assert abs(r["ic_obs"]) < 0.1
    assert r["p_value"] > 0.05
    assert r["significant"] is False


def test_permutation_seed_reproducible():
    f, lab = _make_factor_panel(strong=True)
    a = permutation_ic_test(f, lab, n_permutations=100, seed=7)
    b = permutation_ic_test(f, lab, n_permutations=100, seed=7)
    assert a["p_value"] == b["p_value"]
    assert a["perm_ci"] == b["perm_ci"]


def test_mc_cache_lru():
    from app.services.quant.monte_carlo import mc_cache_clear, mc_cache_get, mc_cache_set

    mc_cache_clear()
    key = (1, 100, 20, 0.9)
    assert mc_cache_get(key) is None
    mc_cache_set(key, {"a": 1})
    assert mc_cache_get(key) == {"a": 1}
    mc_cache_clear()
    assert mc_cache_get(key) is None


def test_deep_analyze_includes_permutation():
    """深度分析 summary 应带出置换检验 perm_pvalue（强因子显著）。"""
    from app.services.quant import factor_eval

    factor_df, label_df = _make_factor_panel(strong=True)
    close_df = factor_df["factor"].rename("$close").to_frame()

    with patch.object(factor_eval, "load_factor_values", return_value=factor_df), \
            patch.object(factor_eval, "load_label", return_value=label_df), \
            patch.object(factor_eval, "init_qlib", return_value=None), \
            patch.object(factor_eval, "_load_instrument_spans",
                         return_value=["sh000001"]), \
            patch("qlib.data.D") as mock_d, \
            patch.object(factor_eval, "compute_ic_distribution",
                         return_value={"stats": {"mean": 0.1, "std": 0.05}}), \
            patch.object(factor_eval, "compute_ic_timeseries",
                         return_value={"ic_series": [0.1] * 20}), \
            patch.object(factor_eval, "compute_ic_significance",
                         return_value={"t_stat": 3.0, "p_value": 0.01,
                                       "significant": True}), \
            patch.object(factor_eval, "compute_quantile_nav_by_horizon",
                         return_value={"long_short_annual_return": 0.2,
                                       "monotonicity": 0.8}), \
            patch.object(factor_eval, "compute_turnover_curve",
                         return_value={"avg_turnover": 0.3,
                                       "annual_turnover": 75.0}), \
            patch.object(factor_eval, "compute_decay",
                         return_value={1: 0.1, 5: 0.05}):
        mock_d.features.return_value = close_df
        res = factor_eval.deep_analyze_factor(
            "TestFactor", "2024-01-01", "2024-06-30", universe="csi300"
        )

    s = res["summary"]
    assert "perm_pvalue" in s
    assert s["perm_significant"] is True
    assert s["perm_n"] == 500
