"""组合绩效报告测试：quantstats。"""
import numpy as np
import pandas as pd
import pytest

from app.services.quant.portfolio_report import generate_portfolio_report


@pytest.fixture
def returns_series():
    rng = np.random.default_rng(0)
    idx = pd.bdate_range("2024-01-01", periods=160)
    # 弱正 alpha + 噪声
    returns = pd.Series(rng.normal(0.001, 0.012, 160), index=idx)
    returns.iloc[0] = 0.0
    return returns


class TestPortfolioReport:
    def test_metrics_computed(self, returns_series):
        report = generate_portfolio_report(returns_series, generate_html=False)
        assert report["n_obs"] == 160
        m = report["metrics"]
        # quantstats 核心指标
        for key in ["sharpe", "sortino", "calmar", "cagr", "annual_volatility",
                    "max_drawdown", "win_rate", "var_95", "tail_ratio"]:
            assert key in m, f"缺少指标 {key}"
        assert m["sharpe"] is not None
        assert m["max_drawdown"] is not None and m["max_drawdown"] <= 0
        assert report["html_report"] is None  # generate_html=False
        assert report["start_date"] and report["end_date"]

    def test_benchmark_metrics(self, returns_series):
        bench = pd.Series(
            np.random.default_rng(1).normal(0.0005, 0.01, 160),
            index=returns_series.index,
        )
        report = generate_portfolio_report(
            returns_series, benchmark=bench, generate_html=False,
        )
        m = report["metrics"]
        for key in ["beta", "alpha", "rsq", "correlation"]:
            assert key in m, f"缺少基准指标 {key}"

    def test_html_report_not_generated(self, returns_series):
        """generate_html=False 时不生成 HTML。"""
        report = generate_portfolio_report(returns_series, generate_html=False)
        assert report["html_report"] is None

    def test_empty_returns(self):
        report = generate_portfolio_report(pd.Series(dtype=float), generate_html=False)
        assert report["n_obs"] == 0
        assert "error" in report
