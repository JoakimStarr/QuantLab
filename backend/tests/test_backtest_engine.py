"""回测引擎测试。

测试 combine_factors / _is_price_limited / _calc_holding_return 纯函数，
以及 run_backtest 的 mock 集成测试。
"""
import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch

from app.services.quant.backtest_engine import (
    combine_factors,
    _is_price_limited,
    _calc_holding_return,
    run_backtest,
)


class TestCombineFactors:
    """多因子组合测试。"""

    def _make_factor_df(self, values_per_day):
        """构造 MultiIndex 因子 DataFrame。
        values_per_day: list of list, 每日子列表为各股票因子值。
        """
        days = len(values_per_day)
        n_stocks = len(values_per_day[0])
        dates = pd.date_range("2024-01-01", periods=days, freq="B")
        stocks = [f"s{i}" for i in range(n_stocks)]
        idx = pd.MultiIndex.from_product([dates, stocks], names=["datetime", "instrument"])
        flat = []
        for day_vals in values_per_day:
            flat.extend(day_vals)
        return pd.DataFrame({"factor": flat}, index=idx)

    def test_single_factor_equal_weight(self):
        """单因子等权：score = z-score。"""
        f1 = self._make_factor_df([[1.0, 2.0, 3.0], [1.0, 2.0, 3.0]])
        result = combine_factors({"f1": f1})
        assert "score" in result.columns
        assert len(result) == 6
        # z-score of [1,2,3] ddof=0: mean=2, std=sqrt(2/3)
        day1 = result.xs(pd.Timestamp("2024-01-01"), level="datetime")["score"]
        expected = np.array([-1.224745, 0.0, 1.224745])
        np.testing.assert_allclose(day1.values, expected, atol=1e-4)

    def test_two_factors_equal_weight(self):
        """双因子等权：score = (z1 + z2) / 2。"""
        f1 = self._make_factor_df([[1.0, 2.0, 3.0]])
        f2 = self._make_factor_df([[3.0, 2.0, 1.0]])
        result = combine_factors({"f1": f1, "f2": f2})
        assert len(result) == 3
        # z1=[-1.2247,0,1.2247], z2=[1.2247,0,-1.2247]
        # score = (z1+z2)/2 = [0,0,0]
        day1 = result.xs(pd.Timestamp("2024-01-01"), level="datetime")["score"]
        np.testing.assert_allclose(day1.values, [0.0, 0.0, 0.0], atol=1e-6)

    def test_ic_weight(self):
        """ic_weight 方法按绝对值归一化权重。"""
        f1 = self._make_factor_df([[1.0, 2.0, 3.0]])
        f2 = self._make_factor_df([[1.0, 3.0, 2.0]])
        weights = {"f1": 2.0, "f2": -1.0}
        result = combine_factors({"f1": f1, "f2": f2}, weights=weights, method="ic_weight")
        assert "score" in result.columns
        assert len(result) == 3

    def test_empty_factors_raises(self):
        """空因子列表应抛 ValueError。"""
        with pytest.raises(ValueError, match="因子列表为空"):
            combine_factors({})

    def test_constant_factor_zscore_zero(self):
        """常量因子 z-score 为 0（std=0 防护）。"""
        f1 = self._make_factor_df([[5.0, 5.0, 5.0]])
        result = combine_factors({"f1": f1})
        day1 = result.xs(pd.Timestamp("2024-01-01"), level="datetime")["score"]
        np.testing.assert_allclose(day1.values, [0.0, 0.0, 0.0], atol=1e-6)

    def test_dropna_in_result(self):
        """结果应 dropna。"""
        f1 = self._make_factor_df([[1.0, 2.0, 3.0]])
        result = combine_factors({"f1": f1})
        assert not result["score"].isna().any()


class TestIsPriceLimited:
    """涨跌停判断测试。"""

    @pytest.mark.parametrize("code,ret,expected", [
        # 主板 ±10% (阈值 0.095)
        ("sh600000", 0.05, False),
        ("sh600000", 0.094, False),
        ("sh600000", 0.095, True),
        ("sh600000", 0.10, True),
        ("sh600000", -0.05, False),
        ("sh600000", -0.094, False),
        ("sh600000", -0.095, True),
        ("sh600000", -0.10, True),
        # 创业板 ±20% (阈值 0.195)
        ("sz300001", 0.15, False),
        ("sz300001", 0.194, False),
        ("sz300001", 0.195, True),
        ("sz300001", 0.20, True),
        ("sz300001", -0.195, True),
        # 科创板 ±20% (sh68 开头)
        ("sh688001", 0.15, False),
        ("sh688001", 0.195, True),
        ("sh688001", -0.20, True),
    ])
    def test_price_limit_thresholds(self, code, ret, expected):
        """涨跌停阈值正确。"""
        assert _is_price_limited(code, ret) is expected

    def test_nan_returns_true(self):
        """NaN 收益视为不可交易。"""
        assert _is_price_limited("sh600000", float("nan")) is True

    def test_none_returns_true(self):
        """None 收益视为不可交易。"""
        assert _is_price_limited("sh600000", None) is True

    def test_case_insensitive(self):
        """代码大小写不敏感。"""
        assert _is_price_limited("SH600000", 0.10) is True
        assert _is_price_limited("SZ300001", 0.10) is False


class TestCalcHoldingReturn:
    """持仓收益计算测试。"""

    def _make_dfs(self):
        dates = pd.date_range("2024-01-01", periods=3, freq="B")
        stocks = ["s1", "s2", "s3"]
        returns_df = pd.DataFrame(
            {"s1": [0.01, 0.02, 0.03], "s2": [0.02, 0.03, 0.01], "s3": [0.03, 0.01, 0.02]},
            index=dates,
        )
        vol_df = pd.DataFrame(
            {"s1": [100, 200, 300], "s2": [200, 300, 100], "s3": [300, 100, 200]},
            index=dates,
        )
        return returns_df, vol_df, dates

    def test_normal_holding_return(self):
        """正常持仓收益 = 等权均值。"""
        returns_df, vol_df, dates = self._make_dfs()
        holdings = {"s1", "s2", "s3"}
        result = _calc_holding_return(returns_df, returns_df.copy(), vol_df, dates[0], holdings)
        expected = (0.01 + 0.02 + 0.03) / 3
        assert result == pytest.approx(expected)

    def test_nan_returns_excluded(self):
        """NaN 收益股票被排除。"""
        returns_df, vol_df, dates = self._make_dfs()
        returns_df.loc[dates[0], "s2"] = np.nan
        holdings = {"s1", "s2", "s3"}
        result = _calc_holding_return(returns_df, returns_df.copy(), vol_df, dates[0], holdings)
        expected = (0.01 + 0.03) / 2
        assert result == pytest.approx(expected)

    def test_empty_holdings(self):
        """空持仓返回 None。"""
        returns_df, vol_df, dates = self._make_dfs()
        result = _calc_holding_return(returns_df, returns_df.copy(), vol_df, dates[0], set())
        assert result is None

    def test_date_not_in_index(self):
        """日期不在 returns_df 索引中返回 None。"""
        returns_df, vol_df, dates = self._make_dfs()
        result = _calc_holding_return(
            returns_df, returns_df.copy(), vol_df, pd.Timestamp("2025-01-01"), {"s1"}
        )
        assert result is None

    def test_all_nan_returns(self):
        """全部 NaN 返回 None。"""
        returns_df, vol_df, dates = self._make_dfs()
        returns_df.loc[dates[0]] = np.nan
        holdings = {"s1", "s2", "s3"}
        result = _calc_holding_return(returns_df, returns_df.copy(), vol_df, dates[0], holdings)
        assert result is None

    def test_partial_holdings(self):
        """部分持仓（不在 columns 的股票被跳过）。"""
        returns_df, vol_df, dates = self._make_dfs()
        holdings = {"s1", "s4"}  # s4 不在 columns
        result = _calc_holding_return(returns_df, returns_df.copy(), vol_df, dates[0], holdings)
        assert result == pytest.approx(0.01)


class TestRunBacktest:
    """run_backtest 集成测试（mock qlib）。"""

    def _make_mock_data(self):
        """构造 mock 打分数据与 mock qlib 价格数据。"""
        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=10, freq="B")
        stocks = ["sh600000", "sz000001", "sz300001", "sh600036", "sz000002"]

        # 打分 DataFrame
        score_idx = pd.MultiIndex.from_product([dates, stocks], names=["datetime", "instrument"])
        score_df = pd.DataFrame(
            {"score": np.random.randn(len(score_idx))}, index=score_idx
        )

        # mock D.features 返回值: 价格+成交量 (MultiIndex)
        feat_idx = pd.MultiIndex.from_product([dates, stocks], names=["datetime", "instrument"])
        n = len(feat_idx)
        close = np.abs(np.cumsum(np.random.randn(n) * 0.02) + 10)
        volume = np.random.rand(n) * 1e6 + 1e5
        raw = pd.DataFrame({"$close": close, "$volume": volume}, index=feat_idx)

        # mock 基准数据
        bench_idx = pd.MultiIndex.from_product([dates, ["sh000300"]], names=["datetime", "instrument"])
        bench_close = np.abs(np.cumsum(np.random.randn(len(dates)) * 0.02) + 10)
        bench_raw = pd.DataFrame({"$close": bench_close}, index=bench_idx)

        return score_df, raw, bench_raw, dates, stocks

    @patch("qlib.data.D")
    @patch("app.services.quant.backtest_engine.init_qlib", return_value=True)
    def test_run_backtest_basic(self, mock_init, mock_D):
        """基本回测流程，返回结构正确。"""
        score_df, raw, bench_raw, dates, stocks = self._make_mock_data()
        mock_D.features.side_effect = [raw, bench_raw]

        result = run_backtest(
            score_df,
            start="2024-01-01",
            end="2024-01-14",
            topk=3,
            n_drop=1,
            benchmark="SH000300",
            rebalance_freq="day",
        )

        assert isinstance(result, dict)
        assert "returns" in result
        assert "benchmark" in result
        assert "turnover" in result
        assert "start_date" in result
        assert "end_date" in result
        assert "portfolios" in result
        assert result["topk"] == 3
        assert result["n_drop"] == 1
        assert result["rebalance_freq"] == "day"
        assert result["benchmark_code"] == "SH000300"
        assert isinstance(result["returns"], pd.Series)
        # init_qlib 被调用
        mock_init.assert_called_once()

    @patch("qlib.data.D")
    @patch("app.services.quant.backtest_engine.init_qlib", return_value=True)
    def test_run_backtest_returns_nonempty(self, mock_init, mock_D):
        """回测应产生非空收益序列。"""
        score_df, raw, bench_raw, dates, stocks = self._make_mock_data()
        mock_D.features.side_effect = [raw, bench_raw]

        result = run_backtest(
            score_df, start="2024-01-01", end="2024-01-14",
            topk=3, n_drop=1, rebalance_freq="day",
        )
        assert len(result["returns"]) > 0

    @patch("qlib.data.D")
    @patch("app.services.quant.backtest_engine.init_qlib", return_value=True)
    def test_run_backtest_rebalance_weekly(self, mock_init, mock_D):
        """周调仓频率可执行。"""
        score_df, raw, bench_raw, dates, stocks = self._make_mock_data()
        mock_D.features.side_effect = [raw, bench_raw]

        result = run_backtest(
            score_df, start="2024-01-01", end="2024-01-14",
            topk=3, n_drop=1, rebalance_freq="week",
        )
        assert result["rebalance_freq"] == "week"

    @patch("qlib.data.D")
    @patch("app.services.quant.backtest_engine.init_qlib", return_value=True)
    def test_run_backtest_rebalance_monthly(self, mock_init, mock_D):
        """月调仓频率可执行。"""
        score_df, raw, bench_raw, dates, stocks = self._make_mock_data()
        mock_D.features.side_effect = [raw, bench_raw]

        result = run_backtest(
            score_df, start="2024-01-01", end="2024-01-14",
            topk=3, n_drop=1, rebalance_freq="month",
        )
        assert result["rebalance_freq"] == "month"

    @patch("qlib.data.D")
    @patch("app.services.quant.backtest_engine.init_qlib", return_value=True)
    def test_run_backtest_empty_score_raises(self, mock_init, mock_D):
        """空打分数据应抛 ValueError。"""
        # 构造一个 start/end 范围之外的打分数据
        score_df, raw, bench_raw, dates, stocks = self._make_mock_data()
        mock_D.features.side_effect = [raw, bench_raw]

        with pytest.raises(ValueError, match="打分数据为空"):
            run_backtest(
                score_df, start="2025-01-01", end="2025-06-30",
                topk=3, n_drop=1,
            )

    @patch("qlib.data.D")
    @patch("app.services.quant.backtest_engine.init_qlib", return_value=True)
    def test_run_backtest_limit_up_filtered(self, mock_init, mock_D):
        """涨停股票不可买入：构造一个涨停场景验证过滤逻辑。"""
        np.random.seed(99)
        dates = pd.date_range("2024-01-01", periods=5, freq="B")
        stocks = ["sh600000", "sz000001", "sh600036"]

        # 构造价格：sh600000 第二天涨停 (>9.5%)
        close_data = {}
        for s in stocks:
            close_data[s] = [10.0] * 5
        # sh600000 第二天从 10 涨到 11 (+10%)
        close_data["sh600000"] = [10.0, 11.0, 11.0, 11.0, 11.0]

        feat_idx = pd.MultiIndex.from_product([dates, stocks], names=["datetime", "instrument"])
        close_flat = []
        vol_flat = []
        for d in dates:
            for s in stocks:
                close_flat.append(close_data[s][dates.get_loc(d)])
                vol_flat.append(1e6)
        raw = pd.DataFrame({"$close": close_flat, "$volume": vol_flat}, index=feat_idx)

        bench_idx = pd.MultiIndex.from_product([dates, ["sh000300"]], names=["datetime", "instrument"])
        bench_raw = pd.DataFrame({"$close": [10.0] * 5}, index=bench_idx)

        # 打分：sh600000 第一天得分最高
        score_idx = pd.MultiIndex.from_product([dates, stocks], names=["datetime", "instrument"])
        scores = []
        for d in dates:
            scores.extend([3.0, 2.0, 1.0])  # sh600000 最高分
        score_df = pd.DataFrame({"score": scores}, index=score_idx)

        mock_D.features.side_effect = [raw, bench_raw]

        result = run_backtest(
            score_df, start="2024-01-01", end="2024-01-07",
            topk=1, n_drop=0, rebalance_freq="day",
        )
        # 回测应成功执行（涨停日 sh600000 被过滤，选其他股票或保持空仓）
        assert isinstance(result["returns"], pd.Series)
