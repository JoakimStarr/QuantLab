"""波动率特征测试：GARCH(1,1) 条件波动率 + tsfresh 特征提取。"""
import numpy as np
import pandas as pd
import pytest

from app.services.quant.vol_features import compute_garch_vol, extract_tsfresh_features


@pytest.fixture
def returns_panel():
    rng = np.random.default_rng(1)
    dates = pd.bdate_range("2023-01-01", periods=200)
    # 波动聚集：前 100 日高波动，后 100 日低波动
    vol = np.concatenate([np.full(100, 0.03), np.full(100, 0.008)])
    df = pd.DataFrame({
        "a": rng.normal(0.0005, vol, 200),
        "b": rng.normal(0.0, 0.01, 200),
    }, index=dates)
    return df


class TestGarchVol:
    def test_compute_garch_vol(self, returns_panel):
        vol = compute_garch_vol(returns_panel, min_obs=60, use_cache=False)
        assert vol.shape == returns_panel.shape
        assert not vol.isnull().all().all()  # 有有效值
        # 波动聚集：前半段条件波动率均值应高于后半段
        mid = len(vol) // 2
        first_half = vol.iloc[:mid].mean().mean()
        second_half = vol.iloc[mid:].mean().mean()
        assert first_half > second_half

    def test_min_obs_guard(self, returns_panel):
        vol = compute_garch_vol(returns_panel.iloc[:50], min_obs=60, use_cache=False)
        assert vol.iloc[:, 0].isnull().all()  # 观测不足全 NaN

    def test_point_in_time(self, returns_panel):
        vol = compute_garch_vol(returns_panel.iloc[:, :1], min_obs=60,
                                point_in_time=True, use_cache=False)
        assert not vol.isnull().all().all()


class TestTsfreshFeatures:
    def test_extract_tsfresh_features(self):
        rng = np.random.default_rng(2)
        dates = pd.bdate_range("2023-01-01", periods=60)
        idx = pd.MultiIndex.from_product([dates, ["s1", "s2"]], names=["datetime", "instrument"])
        panel = pd.DataFrame({
            "value": rng.normal(0, 1, len(idx)),
        }, index=idx)
        feats = extract_tsfresh_features(panel, min_obs=30)
        assert not feats.empty
        assert set(feats.index) == {"s1", "s2"}
        # tsfresh 0.21+ 列名约定：<value>__<feature>
        assert any("__" in c for c in feats.columns)

    def test_empty_panel(self):
        feats = extract_tsfresh_features(pd.DataFrame())
        assert feats.empty

    def test_insufficient_obs_dropped(self):
        rng = np.random.default_rng(2)
        dates = pd.bdate_range("2023-01-01", periods=10)
        idx = pd.MultiIndex.from_product([dates, ["s1"]], names=["datetime", "instrument"])
        panel = pd.DataFrame({"value": rng.normal(0, 1, len(idx))}, index=idx)
        feats = extract_tsfresh_features(panel, min_obs=30)
        assert feats.empty  # s1 观测不足被剔除
