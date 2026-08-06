"""因子评价测试。

compute_ic(factor_df, label_df) -> dict  (纯 pandas)
compute_turnover(factor_df) -> float      (依赖 settings.quant.topk)
"""
import pytest
import numpy as np
import pandas as pd

from app.core.config import settings
from app.services.quant.factor_eval import compute_ic, compute_turnover


class TestComputeIC:
    """IC/RankIC/ICIR 计算测试。"""

    def _make_ic_data(self, factor_vals_per_day, label_vals_per_day):
        """构造 MultiIndex 因子+标签 DataFrame。"""
        days = len(factor_vals_per_day)
        n = len(factor_vals_per_day[0])
        dates = pd.date_range("2024-01-01", periods=days, freq="B")
        stocks = [f"s{i}" for i in range(n)]
        idx = pd.MultiIndex.from_product([dates, stocks], names=["datetime", "instrument"])
        factor_flat = []
        label_flat = []
        for d in range(days):
            factor_flat.extend(factor_vals_per_day[d])
            label_flat.extend(label_vals_per_day[d])
        fdf = pd.DataFrame({"factor": factor_flat}, index=idx)
        ldf = pd.DataFrame({"label": label_flat}, index=idx)
        return fdf, ldf

    def test_ic_perfect_positive(self):
        """完美正相关 IC ≈ 1.0。"""
        fdf, ldf = self._make_ic_data(
            [[1, 2, 3, 4]] * 3,
            [[1, 2, 3, 4]] * 3,
        )
        result = compute_ic(fdf, ldf)
        assert result["ic"] == pytest.approx(1.0, abs=1e-6)
        assert result["rank_ic"] == pytest.approx(1.0, abs=1e-6)

    def test_ic_perfect_negative(self):
        """完美负相关 IC ≈ -1.0。"""
        fdf, ldf = self._make_ic_data(
            [[1, 2, 3, 4]] * 3,
            [[4, 3, 2, 1]] * 3,
        )
        result = compute_ic(fdf, ldf)
        assert result["ic"] == pytest.approx(-1.0, abs=1e-6)
        assert result["rank_ic"] == pytest.approx(-1.0, abs=1e-6)

    def test_ic_zero_correlation(self):
        """无相关 IC ≈ 0。"""
        # 构造不相关的 factor 和 label
        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=20, freq="B")
        stocks = [f"s{i}" for i in range(50)]
        idx = pd.MultiIndex.from_product([dates, stocks], names=["datetime", "instrument"])
        n = len(idx)
        fdf = pd.DataFrame({"factor": np.random.randn(n)}, index=idx)
        ldf = pd.DataFrame({"label": np.random.randn(n)}, index=idx)
        result = compute_ic(fdf, ldf)
        assert abs(result["ic"]) < 0.3  # 弱相关
        assert result["n_days"] == 20

    def test_icir_calculation(self):
        """ICIR = IC均值 / IC标准差。"""
        # 3 天：IC = [1.0, -1.0, 1.0]
        fdf, ldf = self._make_ic_data(
            [[1, 2, 3, 4], [1, 2, 3, 4], [1, 2, 3, 4]],
            [[1, 2, 3, 4], [4, 3, 2, 1], [1, 2, 3, 4]],
        )
        result = compute_ic(fdf, ldf)
        # IC = [1.0, -1.0, 1.0], mean = 1/3, std(ddof=1) = sqrt(4/3)
        ic_mean = 1.0 / 3.0
        ic_std = np.std([1.0, -1.0, 1.0], ddof=1)  # sqrt(4/3)
        expected_icir = ic_mean / ic_std
        assert result["icir"] == pytest.approx(expected_icir, abs=1e-3)
        assert result["n_days"] == 3

    def test_ic_n_days_correct(self):
        """n_days 等于有有效 IC 的天数。"""
        fdf, ldf = self._make_ic_data(
            [[1, 2, 3]] * 5,
            [[1, 2, 3]] * 5,
        )
        result = compute_ic(fdf, ldf)
        assert result["n_days"] == 5

    def test_ic_empty_data(self):
        """空数据返回 None 指标。"""
        fdf = pd.DataFrame({"factor": []}, index=pd.MultiIndex.from_tuples([], names=["datetime", "instrument"]))
        ldf = pd.DataFrame({"label": []}, index=pd.MultiIndex.from_tuples([], names=["datetime", "instrument"]))
        result = compute_ic(fdf, ldf)
        assert result["ic"] is None
        assert result["rank_ic"] is None
        assert result["icir"] is None
        assert result["n_days"] == 0

    def test_ic_single_stock_per_day(self):
        """每日单只股票（无法算相关）→ IC 为 NaN 被丢弃。"""
        fdf, ldf = self._make_ic_data(
            [[1], [2], [3]],
            [[1], [2], [3]],
        )
        result = compute_ic(fdf, ldf)
        assert result["n_days"] == 0
        assert result["ic"] is None

    def test_ic_result_keys(self):
        """返回 dict 包含所有必需键。"""
        fdf, ldf = self._make_ic_data([[1, 2, 3]] * 2, [[1, 2, 3]] * 2)
        result = compute_ic(fdf, ldf)
        assert set(result.keys()) == {"ic", "rank_ic", "icir", "ir", "n_days"}


class TestComputeTurnover:
    """换手率计算测试。"""

    def _make_factor_df(self, values_per_day):
        days = len(values_per_day)
        n = len(values_per_day[0])
        dates = pd.date_range("2024-01-01", periods=days, freq="B")
        stocks = [f"s{i}" for i in range(n)]
        idx = pd.MultiIndex.from_product([dates, stocks], names=["datetime", "instrument"])
        flat = []
        for v in values_per_day:
            flat.extend(v)
        return pd.DataFrame({"factor": flat}, index=idx)

    def test_turnover_zero_when_topk_exceeds_universe(self, monkeypatch):
        """topk >= 股票数时换手率为 0。"""
        monkeypatch.setitem(settings.quant, "topk", 10)
        fdf = self._make_factor_df([[5, 4, 3, 2, 1], [1, 2, 3, 4, 5]])
        result = compute_turnover(fdf)
        assert result == 0.0

    def test_turnover_nonzero(self, monkeypatch):
        """topk < 股票数且排名变化时换手率 > 0。"""
        monkeypatch.setitem(settings.quant, "topk", 3)
        # Day1 top3 = {s0,s1,s2}, Day2 top3 = {s2,s3,s4}
        fdf = self._make_factor_df([
            [5, 4, 3, 2, 1],  # top3: s0,s1,s2
            [1, 2, 3, 4, 5],  # top3: s2,s3,s4
        ])
        result = compute_turnover(fdf)
        # overlap = {s2}, turnover = 1 - 1/3 = 0.6667
        assert result == pytest.approx(0.6667, abs=1e-3)

    def test_turnover_full_change(self, monkeypatch):
        """完全换手：topk=2, 两组完全不重叠。"""
        monkeypatch.setitem(settings.quant, "topk", 2)
        fdf = self._make_factor_df([
            [5, 4, 3, 2],  # top2: s0,s1
            [3, 2, 5, 4],  # top2: s2,s3
        ])
        result = compute_turnover(fdf)
        # overlap = {}, turnover = 1 - 0/2 = 1.0
        assert result == pytest.approx(1.0, abs=1e-3)

    def test_turnover_single_day_returns_none(self, monkeypatch):
        """单日数据无法计算换手（无前日）→ None。"""
        monkeypatch.setitem(settings.quant, "topk", 3)
        fdf = self._make_factor_df([[5, 4, 3, 2, 1]])
        result = compute_turnover(fdf)
        assert result is None

    def test_turnover_stable_ranking(self, monkeypatch):
        """排名不变时换手率为 0。"""
        monkeypatch.setitem(settings.quant, "topk", 3)
        fdf = self._make_factor_df([
            [5, 4, 3, 2, 1],
            [5, 4, 3, 2, 1],
        ])
        result = compute_turnover(fdf)
        assert result == 0.0

    def test_turnover_multi_day_average(self, monkeypatch):
        """多日换手率取均值。"""
        monkeypatch.setitem(settings.quant, "topk", 1)
        fdf = self._make_factor_df([
            [5, 4, 3, 2, 1],  # top1: s0
            [1, 2, 3, 4, 5],  # top1: s4  → turnover = 1.0
            [5, 4, 3, 2, 1],  # top1: s0  → turnover = 1.0
        ])
        result = compute_turnover(fdf)
        assert result == pytest.approx(1.0, abs=1e-3)


# ---------- compute_decay / compute_quantile_returns 索引序修复回归 ----------
# qlib D.features 返回 (instrument, datetime) 索引；alphalens 要求 (date, asset)。
# 旧实现只 set_names 不 swaplevel，导致 compute_decay 恒返回 {}、分层分析恒报错。


def _make_qlib_style_data(days=40, n=30, seed=7):
    """构造 qlib 风格的 (instrument, datetime) 因子/标签/close DataFrame。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=days, freq="B")
    stocks = [f"s{i:03d}" for i in range(n)]
    idx = pd.MultiIndex.from_product([stocks, dates], names=["instrument", "datetime"])

    factor_vals = rng.standard_normal(len(idx))
    label_vals = rng.standard_normal(len(idx)) * 0.02
    # close：每只股票从各自 base 累计 1+label 的近似价格
    lab2d = label_vals.reshape(n, days)
    prices = []
    for s in range(n):
        base = rng.uniform(8, 30)
        prices.extend(base * np.cumprod(1 + lab2d[s]))

    fdf = pd.DataFrame({"factor": factor_vals}, index=idx)
    ldf = pd.DataFrame({"label": label_vals}, index=idx)
    cdf = pd.DataFrame({"$close": prices}, index=idx)
    return fdf, ldf, cdf


class TestComputeDecayIndexOrder:
    """compute_decay 必须返回非空衰减序列（回归：索引未换序导致恒为 {}）。"""

    def test_decay_returns_series_with_qlib_index(self):
        from app.services.quant.factor_eval import compute_decay

        fdf, ldf, cdf = _make_qlib_style_data()
        decay = compute_decay(fdf, ldf, preloaded_close_df=cdf)
        assert isinstance(decay, dict)
        assert decay, "compute_decay 不应返回空 dict（索引换序后应能算出各 lag IC）"
        assert 1 in decay and 10 in decay


class TestQuantileReturnsIndexOrder:
    """compute_quantile_returns 必须正常分组（回归：索引未换序导致 alphalens 报错）。"""

    def test_quantile_returns_works_with_qlib_index(self):
        from app.services.quant.factor_eval import compute_quantile_returns

        fdf, ldf, _cdf = _make_qlib_style_data()
        result = compute_quantile_returns(fdf, ldf, n_groups=5)
        assert result.get("error") is None, f"不应报错: {result.get('error')}"
        assert "group_returns" in result
        assert result.get("n_groups") == 5
        assert len(result.get("group_returns", {})) == 5


class TestLoadFactorValuesEtfNeutralizeSkip:
    """ETF 标的池加载因子值时跳过市值/行业中性化（S3）。"""

    def _fake_feature_df(self):
        import numpy as np
        dates = pd.date_range("2024-01-01", periods=5, freq="B")
        idx = pd.MultiIndex.from_product([dates, ["sh510300"]], names=["datetime", "instrument"])
        return pd.DataFrame({"factor": [1.0, 2.0, 3.0, 4.0, 5.0]}, index=idx)

    def test_etf_universe_skips_neutralize(self):
        from unittest.mock import MagicMock, patch
        from app.services.quant import factor_eval as fe

        fake_df = self._fake_feature_df()
        mock_d = MagicMock()
        mock_d.features.return_value = fake_df
        with patch.object(fe, "init_qlib"), \
             patch.object(fe, "_load_instrument_spans", return_value=["sh510300"]), \
             patch("qlib.data.D", mock_d), \
             patch("app.services.factor.neutralize.industry_neutralize") as mock_ind:
            df = fe.load_factor_values(
                "$close/Ref($close,5)-1", "2024-01-01", "2024-01-10",
                universe="etf_curated", neutralize="industry",
            )
        assert "factor_neutralized" not in df.columns
        assert list(df["factor"]) == [1.0, 2.0, 3.0, 4.0, 5.0]
        mock_ind.assert_not_called()

    def test_stock_universe_still_neutralizes(self):
        from unittest.mock import MagicMock, patch
        from app.services.quant import factor_eval as fe

        fake_df = self._fake_feature_df().copy()
        neutralized = fake_df.copy()
        neutralized["factor_neutralized"] = [0.1, 0.2, 0.3, 0.4, 0.5]
        mock_d = MagicMock()
        mock_d.features.return_value = fake_df
        with patch.object(fe, "init_qlib"), \
             patch.object(fe, "_load_instrument_spans", return_value=["sh600000"]), \
             patch("qlib.data.D", mock_d), \
             patch("app.services.factor.neutralize.industry_neutralize",
                   return_value=neutralized) as mock_ind:
            df = fe.load_factor_values(
                "$close/Ref($close,5)-1", "2024-01-01", "2024-01-10",
                universe="csi300", neutralize="industry",
            )
        mock_ind.assert_called_once()
        assert "factor_neutralized" not in df.columns  # 中性化后替换回 factor
        assert list(df["factor"]) == [0.1, 0.2, 0.3, 0.4, 0.5]
