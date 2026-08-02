"""purged CV 交叉验证与过拟合统计测试（purgedcv 库）。"""
import numpy as np
import pandas as pd


from app.services.quant import purged_cv as pcv


def _synthetic_panel(n_days: int = 40, n_stocks: int = 15, seed: int = 0):
    """合成 (datetime, instrument) 面板：factor 带弱信号，label 为其带噪滞后。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n_days)
    idx = pd.MultiIndex.from_product([dates, [f"s{i}" for i in range(n_stocks)]],
                                     names=["datetime", "instrument"])
    signal = rng.normal(0, 1, n_stocks)
    factor = np.tile(signal, n_days) + rng.normal(0, 0.5, len(idx))
    label = np.tile(signal, n_days) * 0.5 + rng.normal(0, 0.5, len(idx))
    return pd.DataFrame({"factor": factor, "label": label}, index=idx)


class TestPurgedCVIC:
    def test_purged_cv_ic_basic(self, monkeypatch):
        panel = _synthetic_panel()
        monkeypatch.setattr(pcv, "_load_merged", lambda *a, **k: panel)

        res = pcv.purged_cv_ic("test_expr", "2024-01-01", "2024-03-01", horizon=5, n_splits=4)
        assert res["method"] == "purged-kfold"
        assert res["purged"] is True
        assert res["embargoed"] is True
        assert res["n_splits"] == 4
        assert len(res["folds"]) == 4
        assert res["mean_ic"] is not None
        assert res["n_days"] > 0
        assert res["n_total_samples"] == len(panel)
        # 弱信号应为正 IC
        assert res["mean_fold_ic"] > 0
        # 每折信息完整
        for f in res["folds"]:
            assert f["n_train"] > 0 and f["n_test"] > 0
            assert f["ic_mean"] is not None

    def test_purged_cv_empty_data(self, monkeypatch):
        empty = pd.DataFrame(columns=["factor", "label"])
        monkeypatch.setattr(pcv, "_load_merged", lambda *a, **k: empty)
        res = pcv.purged_cv_ic("x", "2024-01-01", "2024-03-01")
        assert res["folds"] == []
        assert res["mean_ic"] is None
        assert "note" in res

    def test_purged_cv_fallback_sequential(self, monkeypatch):
        """purgedcv 不可用时降级为顺序 KFold（不抛异常）。"""
        panel = _synthetic_panel()
        monkeypatch.setattr(pcv, "_load_merged", lambda *a, **k: panel)
        monkeypatch.setattr(pcv, "_PurgedKFold", None)
        res = pcv.purged_cv_ic("x", "2024-01-01", "2024-03-01", n_splits=4)
        assert res["method"] == "sequential-kfold"
        assert res["purged"] is False
        assert res["mean_ic"] is not None


class TestOverfittingStatistics:
    def test_dsr_with_returns(self):
        rng = np.random.default_rng(7)
        returns = rng.normal(0.001, 0.01, 252)
        res = pcv.overfitting_statistics(returns=returns, n_trials=20)
        assert res["method"] == "dsr"
        assert res["dsr"] is not None and 0 <= res["dsr"] <= 1
        assert res["sr_observed"] is not None
        assert res["n_obs"] == 252

    def test_dsr_with_ic_proxy(self):
        rng = np.random.default_rng(7)
        ic = rng.normal(0.02, 0.05, 200)
        res = pcv.overfitting_statistics(ic_series=ic, n_trials=10)
        assert res["method"] == "ic-proxy"
        assert res["dsr"] is not None

    def test_insufficient_data(self):
        res = pcv.overfitting_statistics(returns=[0.01, -0.01])
        assert res["method"] == "insufficient-data"
        assert res["dsr"] is None


class TestProbabilityOfOverfitting:
    def test_pbo(self):
        rng = np.random.default_rng(3)
        # 20 个候选 × 100 期：其中一个明显更强
        paths = rng.normal(0.0, 0.02, (20, 100))
        paths[0] += 0.005
        res = pcv.probability_of_overfitting(paths, n_splits=10)
        assert "pbo" in res
        assert res["n_trials"] == 20
        assert res["n_periods"] == 100

    def test_pbo_bad_shape(self):
        res = pcv.probability_of_overfitting(np.zeros((2, 5)))
        assert res["pbo"] is None
