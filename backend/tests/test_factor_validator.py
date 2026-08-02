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
