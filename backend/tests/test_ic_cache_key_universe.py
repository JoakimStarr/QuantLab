# -*- coding: utf-8 -*-
"""验证 _ic_cache_key 纳入 universe（不同股票池不得命中同一缓存）。"""
from app.services.quant.factor_validator import _ic_cache_key


def test_ic_cache_key_differs_by_universe():
    k1 = _ic_cache_key("$close/Ref($close,1)-1", "2024-01-01", "2024-12-31", 5, universe="csi300")
    k2 = _ic_cache_key("$close/Ref($close,1)-1", "2024-01-01", "2024-12-31", 5, universe="csi500")
    assert k1 != k2, "不同 universe 不应命中同一缓存 key"


def test_ic_cache_key_same_universe_identical():
    k1 = _ic_cache_key("$close/Ref($close,1)-1", "2024-01-01", "2024-12-31", 5, universe="csi300")
    k2 = _ic_cache_key("$close/Ref($close,1)-1", "2024-01-01", "2024-12-31", 5, universe="csi300")
    assert k1 == k2
