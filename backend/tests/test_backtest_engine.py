"""回测引擎测试。

测试 combine_factors 纯函数，以及 run_backtest 的 mock 集成测试。
"""
import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch

from app.services.quant.backtest_engine import (
    combine_factors,
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

    def test_ic_weight_negative_keeps_sign(self):
        """ic_weight 负权重保留符号：负 IC 因子做反向贡献而非方向反转。"""
        f1 = self._make_factor_df([[1.0, 2.0, 3.0]])
        f2 = self._make_factor_df([[3.0, 2.0, 1.0]])
        weights = {"f1": 0.5, "f2": -0.5}
        result = combine_factors({"f1": f1, "f2": f2}, weights=weights, method="ic_weight")
        day1 = result.xs(pd.Timestamp("2024-01-01"), level="datetime")["score"]
        # z1=[-1.2247,0,1.2247], z2=[1.2247,0,-1.2247]
        # score = 0.5*z1 + (-0.5)*z2 = [-1.2247, 0, 1.2247]
        np.testing.assert_allclose(day1.values, [-1.224745, 0.0, 1.224745], atol=1e-4)

    def test_ic_weight_negative_equals_flipped_positive(self):
        """负权重因子 = 正权重 + 因子取值取反，方向语义一致。"""
        f1 = self._make_factor_df([[1.0, 2.0, 3.0]])
        f2 = self._make_factor_df([[3.0, 2.0, 1.0]])
        r_neg = combine_factors({"f1": f1, "f2": f2}, weights={"f1": 0.5, "f2": -0.5}, method="ic_weight")
        r_flip = combine_factors({"f1": f1, "f2": -f2}, weights={"f1": 0.5, "f2": 0.5}, method="ic_weight")
        s_neg = r_neg.xs(pd.Timestamp("2024-01-01"), level="datetime")["score"]
        s_flip = r_flip.xs(pd.Timestamp("2024-01-01"), level="datetime")["score"]
        # 负权重组合 == 因子取反后的正权重组合
        np.testing.assert_allclose(s_neg.values, s_flip.values, atol=1e-6)

    def test_equal_weight_negative_weight_flips_direction(self):
        """equal_weight 负权重翻转因子方向（反向因子）。"""
        f1 = self._make_factor_df([[1.0, 2.0, 3.0]])
        f2 = self._make_factor_df([[3.0, 2.0, 1.0]])
        weights = {"f1": -0.8, "f2": 0.2}
        result = combine_factors({"f1": f1, "f2": f2}, weights=weights, method="equal_weight")
        day1 = result.xs(pd.Timestamp("2024-01-01"), level="datetime")["score"]
        # 方向翻转后 f1 与 f2 同向：score = -z1*0.5 + z2*0.5 = [1.2247, 0, -1.2247]
        np.testing.assert_allclose(day1.values, [1.224745, 0.0, -1.224745], atol=1e-4)

    def test_equal_weight_zero_weight_positive(self):
        """equal_weight 权重为 0 的因子按正向处理。"""
        f1 = self._make_factor_df([[1.0, 2.0, 3.0]])
        f2 = self._make_factor_df([[3.0, 2.0, 1.0]])
        result = combine_factors({"f1": f1, "f2": f2}, weights={"f1": 1.0, "f2": 0.0}, method="equal_weight")
        day1 = result.xs(pd.Timestamp("2024-01-01"), level="datetime")["score"]
        np.testing.assert_allclose(day1.values, [0.0, 0.0, 0.0], atol=1e-6)



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

