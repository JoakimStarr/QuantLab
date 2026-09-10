"""因子验证器测试：Newey-West / BH 多重检验 / 样本分割（真实交易日）。"""
import numpy as np
import pandas as pd
import pytest

from app.services.quant.factor_validator import (
    SampleSplitter,
    bh_corrected_pvalues,
    newey_west_t,
)


class TestSampleSplitter:
    def test_split_ratios(self):
        s = SampleSplitter()
        dates = list(pd.date_range("2024-01-01", periods=100, freq="B").strftime("%Y-%m-%d"))
        segs = s.split(dates)
        assert len(segs["train"]) == 60
        assert len(segs["valid"]) == 20
        assert len(segs["test"]) == 20
        assert segs["train"] + segs["valid"] + segs["test"] == dates

    def test_split_by_dates_keeps_actual_trading_days(self):
        """split_by_dates 用真实交易日序列，结果与之完全一致（无自然日注水）。"""
        s = SampleSplitter()
        actual = pd.date_range("2024-01-01", periods=63, freq="B").strftime("%Y-%m-%d").tolist()
        segs = s.split_by_dates(actual)
        assert len(segs["train"]) == 37
        assert set(segs["train"]).issubset(actual)
        assert set(segs["train"]) & set(segs["test"]) == set()
        assert segs["train"] + segs["valid"] + segs["test"] == actual

    def test_split_by_dates_empty(self):
        segs = SampleSplitter().split_by_dates([])
        assert segs == {"train": [], "valid": [], "test": []}

    def test_split_dates_returns_ranges(self):
        s = SampleSplitter()
        result = s.split_dates("2024-01-01", "2024-12-31")
        assert set(result.keys()) == {"train", "valid", "test"}
        for key, (start, end) in result.items():
            assert start <= end


class TestNeweyWestT:
    def test_positive_ic_significant(self):
        np.random.seed(42)
        series = pd.Series(np.random.normal(0.02, 0.05, 200))
        t, p = newey_west_t(series, lags=5)
        assert t is not None
        assert t > 0
        assert 0 < p < 0.05

    def test_autocorrelated_series_reduces_t(self):
        """自相关序列（重叠标签）下 NW t 显著低于朴素 t。"""
        np.random.seed(0)
        innovations = np.random.normal(0.0, 0.05, 300)
        # 移动平均制造重叠性自相关（模拟重叠前向收益标签）
        autocorr = np.convolve(innovations, np.ones(10) / 10.0, mode="valid")
        ic = pd.Series(autocorr + 0.01)
        t_nw, _ = newey_west_t(ic, lags=9)
        # 朴素 t（独立假设）
        mu = ic.mean()
        t_naive = mu / (ic.std(ddof=1) / np.sqrt(len(ic)))
        assert t_nw is not None
        assert abs(t_nw) < abs(t_naive)

    def test_zero_mean_returns_zero(self):
        t, p = newey_west_t(pd.Series(np.zeros(50)))
        assert t == 0.0 and p == 1.0

    def test_short_series_returns_none(self):
        assert newey_west_t(pd.Series([1.0, 2.0])) == (None, None)

    def test_nan_dropped(self):
        t, p = newey_west_t(pd.Series([0.01, 0.02, np.nan, 0.03, 0.015, 0.025, 0.02]))
        assert t is not None
        assert abs(t) > 0


class TestBHCorrectedPValues:
    def test_increasing_pvalues_stay_ordered(self):
        pvals = [0.001, 0.01, 0.02, 0.1, 0.5]
        q = bh_corrected_pvalues(pvals)
        assert len(q) == len(pvals)
        assert all(q[i] <= q[i + 1] for i in range(len(q) - 1))

    def test_qvalue_never_below_pvalue(self):
        pvals = [0.001, 0.005, 0.05, 0.2]
        q = bh_corrected_pvalues(pvals)
        for p, qv in zip(pvals, q):
            assert qv >= p

    def test_none_preserved(self):
        q = bh_corrected_pvalues([0.01, None, 0.1])
        assert q[1] is None
        assert q[0] is not None and q[2] is not None

    def test_all_none(self):
        assert bh_corrected_pvalues([None, None]) == [None, None]

    def test_empty(self):
        assert bh_corrected_pvalues([]) == []

    def test_single_pvalue_unchanged(self):
        assert bh_corrected_pvalues([0.05]) == [0.05]

    def test_double_smallest_stays_smallest(self):
        """最小 p 值经过多重检验后仍应显著（保守性检查）。"""
        pvals = [0.001, 0.03, 0.04, 0.05, 0.09, 0.2, 0.3, 0.4]
        q = bh_corrected_pvalues(pvals)
        assert q[pvals.index(0.001)] < 0.05

    def test_out_of_range_clipped(self):
        q = bh_corrected_pvalues([-0.1, 1.5, 0.05])
        assert all(0.0 <= x <= 1.0 for x in q)


class TestCrossBatchBH:
    """A6：跨批次累计的多重检验校正。"""

    def test_accumulated_history_makes_q_more_conservative(self):
        from collections import deque

        from app.services.quant.factor_validator import cross_batch_bh_correct

        reg = deque(maxlen=10000)
        q1 = cross_batch_bh_correct([0.04], registry=reg)
        assert q1 == [pytest.approx(0.04)]
        # 第二批：池里已有 1 个历史检验，m=3 → 本批 0.04 的 q 升至 0.06
        q2 = cross_batch_bh_correct([0.04, 0.5], registry=reg)
        assert q2[0] > q1[0]
        assert q2[0] == pytest.approx(0.06)
        assert q2[1] == pytest.approx(0.5)

    def test_none_preserved_and_length_kept(self):
        from collections import deque

        from app.services.quant.factor_validator import cross_batch_bh_correct

        reg = deque(maxlen=10000)
        out = cross_batch_bh_correct([0.1, None, 0.2], registry=reg)
        assert len(out) == 3
        assert out[1] is None
        assert out[0] is not None and out[2] is not None

    def test_reset_clears_module_pool(self):
        from app.services.quant.factor_validator import (
            cross_batch_bh_correct,
            reset_cross_batch_registry,
        )

        reset_cross_batch_registry()
        q1 = cross_batch_bh_correct([0.05])
        reset_cross_batch_registry()
        q2 = cross_batch_bh_correct([0.05])
        # 重置后两批互不可见，q 相同（各自 m=1）
        assert q1 == q2 == [pytest.approx(0.05)]


class TestTestICGate:
    """A6：test_ic 方向一致性门禁（通过 monkeypatch 纯逻辑验证，无 DB）。"""

    N_STOCKS = 12
    N_DAYS = 60

    @staticmethod
    def _make_dfs(flip_test: bool):
        rng = np.random.default_rng(7)
        dates = pd.date_range("2024-01-01", periods=TestTestICGate.N_DAYS, freq="B")
        stocks = [f"s{j}" for j in range(TestTestICGate.N_STOCKS)]
        idx = pd.MultiIndex.from_product([dates, stocks], names=["datetime", "instrument"])
        n = len(idx)
        codes = np.tile(np.arange(TestTestICGate.N_STOCKS) + 1, TestTestICGate.N_DAYS) / TestTestICGate.N_STOCKS
        day_of = np.repeat(np.arange(TestTestICGate.N_DAYS), TestTestICGate.N_STOCKS)
        noise = rng.normal(0, 0.02, n)
        label = codes + noise
        # 测试段（后 12 天）标签与因子反向 → IC 为负
        if flip_test:
            label = np.where(day_of >= 48, -codes + noise, label)
        fdf = pd.DataFrame({"factor": codes}, index=idx)
        ldf = pd.DataFrame({"label": label}, index=idx)
        return fdf, ldf

    def _run(self, monkeypatch, flip_test: bool) -> dict:
        from app.services.quant import factor_eval as fe
        from app.services.quant.factor_validator import (
            clear_ic_cache,
            evaluate_factor_with_validation,
        )

        fdf, ldf = self._make_dfs(flip_test)
        monkeypatch.setattr(fe, "load_factor_values", lambda *a, **k: fdf, raising=False)
        monkeypatch.setattr(fe, "load_label", lambda *a, **k: ldf, raising=False)
        clear_ic_cache()
        return evaluate_factor_with_validation(
            "test_expr", "2024-01-01", "2024-12-31",
            ic_threshold=0.01, significance_alpha=0.05,
            stability_threshold=0.5, positive_ratio_threshold=0.55,
            decay_threshold=-0.01,
        )

    def test_consistent_direction_passes(self, monkeypatch):
        result = self._run(monkeypatch, flip_test=False)
        assert result["test_ic_pass"] is True
        assert result["passed"] is True, result["fail_reasons"]
        assert result["fail_reasons"] == []

    def test_flipped_test_ic_fails(self, monkeypatch):
        result = self._run(monkeypatch, flip_test=True)
        assert result["test_ic_pass"] is False
        assert result["passed"] is False
        assert any("方向不一致" in r for r in result["fail_reasons"])
        # valid 段本身表现很好，失败只能来自 test 门禁
        assert result["valid_ic"] > 0.5
