# -*- coding: utf-8 -*-
"""后复权变换（data_adjusted）单元测试。

覆盖：
- compute_hfq_factor：正常日 e=1；除权日 e>1 且 hfq 日收益 = 1+change
- validate_change_consistency：一致性 guard 定位异常 bar
- apply_hfq_transform：缩放 OHLC/preclose + factor=A，幂等，增量模式
- rebuild_stock_hfq / rebuild_bins_hfq：fake bin 上 dry-run/apply/幂等/guard 跳过
"""
import os

import numpy as np
import pandas as pd
import pytest

from app.services.data.data_adjusted import (
    _read_bin_array,
    _write_bin_array,
    apply_hfq_transform,
    compute_hfq_factor,
    is_stock_dir,
    rebuild_bins_hfq,
    rebuild_stock_hfq,
    validate_change_consistency,
)


# ------------------------------------------------------------ factor math

def test_normal_days_factor_is_one():
    """无除权（prev_close == preclose）→ e 全为 1，A 恒为 1。"""
    close = pd.Series([10.0, 10.2, 10.4, 10.6])
    preclose = pd.Series([np.nan, 10.0, 10.2, 10.4])
    a = compute_hfq_factor(close, preclose)
    assert a.tolist() == pytest.approx([1.0, 1.0, 1.0, 1.0])


def test_ex_dividend_day_factor_reproduces_change():
    """除权日 e = 前收/preclose > 1；hfq 日收益 == 1 + change。"""
    # day1 close=10；day2 除权 preclose=9.5，close=10.0
    close = pd.Series([10.0, 10.0])
    preclose = pd.Series([np.nan, 9.5])
    a = compute_hfq_factor(close, preclose)
    assert a.iloc[0] == pytest.approx(1.0)
    assert a.iloc[1] == pytest.approx(10.0 / 9.5)

    hfq = close.to_numpy() * a.to_numpy()
    change = close.to_numpy() / preclose.to_numpy() - 1.0
    assert hfq[1] / hfq[0] == pytest.approx(1.0 + change[1])


def test_first_day_and_invalid_preclose_default_to_one():
    close = pd.Series([10.0, 11.0, 12.0])
    preclose = pd.Series([np.nan, 0.0, 11.0])  # 首日无前收 + 除零
    a = compute_hfq_factor(close, preclose)
    assert a.tolist() == pytest.approx([1.0, 1.0, 1.0])


# ------------------------------------------------------------ guard

def test_validate_change_consistency_flags_bad_bar():
    close = pd.Series([10.0, 11.0, 12.0, 13.0])
    preclose = pd.Series([np.nan, 10.0, 11.0, 20.0])
    change = pd.Series([0.0, 0.10, 0.0909, 0.0])  # 最后一根与 close/preclose 不符
    bad = validate_change_consistency(close, preclose, change, tol=1e-3)
    assert 3 in bad
    assert 1 not in bad


def test_validate_change_consistency_skips_invalid_rows():
    close = pd.Series([10.0, np.nan])
    preclose = pd.Series([np.nan, 10.0])
    change = pd.Series([0.0, 0.5])
    assert validate_change_consistency(close, preclose, change) == []


# ------------------------------------------------------------ transform

def _raw_df():
    return pd.DataFrame({
        "open": [10.0, 10.0],
        "high": [10.0, 10.0],
        "low": [10.0, 10.0],
        "close": [10.0, 10.0],
        "preclose": [np.nan, 9.5],
        "volume": [100.0, 200.0],
        "amount": [1000.0, 2000.0],
        "change": [0.0, 10.0 / 9.5 - 1.0],
        "tradable": [1.0, 1.0],
    })


def test_apply_hfq_transform_scales_prices_and_sets_factor():
    out = apply_hfq_transform(_raw_df())
    a1 = 10.0 / 9.5
    assert out["close"].tolist() == pytest.approx([10.0, 10.0 * a1])
    assert out["preclose"].tolist() == pytest.approx([np.nan, 9.5 * a1], nan_ok=True)
    assert out["factor"].tolist() == pytest.approx([1.0, a1])
    # volume/amount/change 不变
    assert out["volume"].tolist() == [100.0, 200.0]
    assert out["change"].tolist() == pytest.approx([0.0, 10.0 / 9.5 - 1.0])
    assert out["tradable"].tolist() == [1.0, 1.0]


def test_apply_hfq_transform_is_idempotent():
    once = apply_hfq_transform(_raw_df())
    twice = apply_hfq_transform(once)
    for c in ("open", "high", "low", "close", "preclose", "factor"):
        assert twice[c].to_numpy() == pytest.approx(once[c].to_numpy(), nan_ok=True)


def test_apply_hfq_transform_incremental_uses_base_factor():
    """增量模式：新 bar 的 A = base_factor × 段内累计（含边界 preclose）。"""
    # 已存最后一根 bar：原始 close=20，factor=base
    base = 2.0
    prev_raw_close = 20.0
    # 新增一根 bar：除权 preclose=19.0，close=20.0
    new = pd.DataFrame({
        "open": [20.0], "high": [20.0], "low": [20.0], "close": [20.0],
        "preclose": [19.0], "volume": [1.0], "amount": [1.0],
        "change": [20.0 / 19.0 - 1.0],
    })
    out = apply_hfq_transform(new, base_factor=base, prev_raw_close=prev_raw_close)
    a_expected = base * (20.0 / 19.0)
    assert out["factor"].iloc[0] == pytest.approx(a_expected)
    assert out["close"].iloc[0] == pytest.approx(20.0 * a_expected)


# ------------------------------------------------------------ bin rebuild

def _write_stock_bins(feat, close, preclose, change, with_factor=None):
    os.makedirs(feat, exist_ok=True)
    n = len(close)
    for fld, vals in (
        ("open", close), ("high", close), ("low", close),
        ("close", close), ("preclose", preclose), ("change", change),
    ):
        _write_bin_array(os.path.join(feat, f"{fld}.day.bin"),
                         np.asarray(vals, dtype="<f4"), 0)
    if with_factor is not None:
        _write_bin_array(os.path.join(feat, "factor.day.bin"),
                         np.asarray(with_factor, dtype="<f4"), 0)
    assert n == len(close)


def _ex_div_stock(feat):
    # day1 raw close=10；day2 除权 preclose=9.5 close=10
    close = [10.0, 10.0, 10.0]
    preclose = [np.nan, 9.5, 9.8]
    change = [0.0, 10.0 / 9.5 - 1.0, 10.0 / 9.8 - 1.0]
    _write_stock_bins(feat, close, preclose, change)


def test_rebuild_stock_hfq_dry_run_then_apply(tmp_path, caplog):
    feat = str(tmp_path / "features" / "sh600000")
    _ex_div_stock(feat)

    r = rebuild_stock_hfq(feat, apply=False)
    assert r["ok"] and r["changed"] and not r["applied"]
    # dry-run 未改盘：factor bin 仍不存在
    assert not os.path.exists(os.path.join(feat, "factor.day.bin"))

    r2 = rebuild_stock_hfq(feat, apply=True)
    assert r2["applied"] and r2["changed"]
    close, start = _read_bin_array(os.path.join(feat, "close.day.bin"))
    factor, _ = _read_bin_array(os.path.join(feat, "factor.day.bin"))
    assert start == 0
    assert close[0] == pytest.approx(10.0)
    assert close[1] == pytest.approx(10.0 * (10.0 / 9.5))
    assert factor[1] == pytest.approx(10.0 / 9.5)
    # 长度不变式 4 + 4*n
    assert os.path.getsize(os.path.join(feat, "close.day.bin")) == 4 + 4 * 3


def test_rebuild_stock_hfq_idempotent(tmp_path):
    feat = str(tmp_path / "features" / "sh600000")
    _ex_div_stock(feat)
    rebuild_stock_hfq(feat, apply=True)
    r2 = rebuild_stock_hfq(feat, apply=True)
    assert r2["ok"] and not r2["changed"] and not r2["applied"]


def test_rebuild_stock_hfq_guard_skips_corrupt_preclose(tmp_path):
    feat = str(tmp_path / "features" / "sh600000")
    # change 与 close/preclose 不符 → guard 失败，跳过
    _write_stock_bins(feat, [10.0, 10.0], [np.nan, 9.5], [0.0, 0.5])
    r = rebuild_stock_hfq(feat, apply=True)
    assert r["ok"] is False
    assert r["bad_indices"]
    assert not os.path.exists(os.path.join(feat, "factor.day.bin"))


def test_rebuild_bins_hfq_skips_non_stock_dirs(tmp_path):
    root = tmp_path / "qlib"
    feat_root = root / "features"
    _ex_div_stock(str(feat_root / "sh600000"))
    # 指数目录：仅 OHLCV（无 preclose/change）→ 跳过
    idx = feat_root / "sh000001"
    os.makedirs(idx)
    _write_bin_array(str(idx / "close.day.bin"), np.asarray([1.0, 2.0], dtype="<f4"), 0)
    assert not is_stock_dir(str(idx))
    assert is_stock_dir(str(feat_root / "sh600000"))

    summary = rebuild_bins_hfq(str(root), apply=True)
    assert summary["total"] == 1
    assert summary["applied"] == 1
    # 指数目录未被处理（无 factor bin）
    assert not os.path.exists(str(idx / "factor.day.bin"))
