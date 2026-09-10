"""A6 研究加固测试：walk-forward purge/embargo + AutoML 时序 CV gap。

纯逻辑测试（monkeypatch 掉回测引擎），无 DB 依赖。
"""
import numpy as np
import pandas as pd
import pytest


def _make_score_df(n_days: int = 100, n_stocks: int = 5) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
    stocks = [f"s{j}" for j in range(n_stocks)]
    idx = pd.MultiIndex.from_product([dates, stocks], names=["datetime", "instrument"])
    vals = np.tile(np.arange(n_stocks), n_days).astype(float)
    return pd.DataFrame({"score": vals}, index=idx)


class TestWalkForwardEmbargo:
    def test_test_start_has_embargo_gap(self, monkeypatch):
        """测试窗起点必须严格晚于训练窗终点，且间隔 >= embargo_days 个交易日。"""
        calls = []

        def fake_run_backtest(score_df, start=None, end=None, **kw):
            calls.append({"start": start, "end": end, "topk": kw.get("topk")})
            return {"returns": pd.Series([0.01, 0.005, 0.002] * 10)}

        monkeypatch.setattr(
            "app.services.quant.backtest_engine.run_backtest", fake_run_backtest
        )
        monkeypatch.setattr(
            "app.services.quant.portfolio.analyze_portfolio",
            lambda returns: {"sharpe": 1.0},
        )

        from app.services.quant.walk_forward import run_walk_forward

        result = run_walk_forward(
            _make_score_df(),
            train_window="30D", test_window="20D", step="20D",
            topk_candidates=[2], embargo_days=3,
        )
        assert "error" not in result
        assert result["n_windows"] > 0
        for w in result["windows"]:
            train_end = pd.Timestamp(w["train_end"])
            test_start = pd.Timestamp(w["test_start"])
            assert test_start > train_end
            # 日历间隔 >= 3 天（embargo 3 个交易日只会更长）
            assert (test_start - train_end).days >= 3
            assert w["embargo_days"] == 3

    def test_embargo_zero_still_starts_after_train_end(self, monkeypatch):
        """embargo=0 时也不应与训练窗重叠（交易日对齐，不再取 train_end 当天）。"""
        monkeypatch.setattr(
            "app.services.quant.backtest_engine.run_backtest",
            lambda score_df, start=None, end=None, **kw: {
                "returns": pd.Series([0.01, 0.005] * 5)
            },
        )
        monkeypatch.setattr(
            "app.services.quant.portfolio.analyze_portfolio",
            lambda returns: {"sharpe": 1.0},
        )

        from app.services.quant.walk_forward import run_walk_forward

        result = run_walk_forward(
            _make_score_df(),
            train_window="30D", test_window="20D", step="20D",
            topk_candidates=[2], embargo_days=0,
        )
        assert "error" not in result
        assert result["n_windows"] > 0
        for w in result["windows"]:
            assert pd.Timestamp(w["test_start"]) > pd.Timestamp(w["train_end"])

    def test_embargo_shrinks_window_count(self, monkeypatch):
        """加 embargo 后可用测试窗变少或不变（隔离区消耗了数据）。"""
        monkeypatch.setattr(
            "app.services.quant.backtest_engine.run_backtest",
            lambda score_df, start=None, end=None, **kw: {
                "returns": pd.Series([0.01, 0.005] * 5)
            },
        )
        monkeypatch.setattr(
            "app.services.quant.portfolio.analyze_portfolio",
            lambda returns: {"sharpe": 1.0},
        )

        from app.services.quant.walk_forward import run_walk_forward

        kwargs = dict(
            train_window="40D", test_window="30D", step="30D", topk_candidates=[2],
        )
        r0 = run_walk_forward(_make_score_df(n_days=200), embargo_days=0, **kwargs)
        r5 = run_walk_forward(_make_score_df(n_days=200), embargo_days=5, **kwargs)
        assert r5["n_windows"] <= r0["n_windows"]


class TestTimeSeriesCVEmbargo:
    def test_ts_cv_splits_gap(self):
        """_ts_cv_splits：每折 valid 起点与 train 末尾之间恰好隔 embargo 个样本。"""
        from app.services.mining.automl import _ts_cv_splits

        for embargo in (0, 5):
            splits = _ts_cv_splits(120, n_splits=4, embargo=embargo)
            assert len(splits) == 4
            for train_idx, valid_idx in splits:
                assert len(train_idx) > 0 and len(valid_idx) > 0
                assert valid_idx[0] == train_idx[-1] + embargo + 1

    def test_time_series_cv_eval_runs_with_embargo(self):
        from app.services.mining.automl import time_series_cv_eval

        rng = np.random.default_rng(0)
        X = pd.DataFrame({"f1": rng.normal(size=120), "f2": rng.normal(size=120)})
        y = pd.Series(rng.normal(size=120))

        class _Model:
            def fit(self, Xv, yv):
                self.mean = float(np.mean(yv))
                return self

            def predict(self, Xv):
                return np.full(len(Xv), self.mean)

        res = time_series_cv_eval(lambda: _Model(), X, y, n_splits=3, embargo=5)
        assert set(res.keys()) >= {"mean_ic", "std_ic", "scores"}
        assert len(res["scores"]) == 3
