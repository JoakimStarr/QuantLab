# -*- coding: utf-8 -*-
"""因子 IC 相关矩阵：纯逻辑测试（合成 IC 序列）+ API 参数校验测试（不依赖 DB/qlib 数据）。"""
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from app.services.factor.factor_compare import _CORR_MIN_OVERLAP_DAYS, _build_ic_corr_matrix


def _series(values, start="2024-01-01"):
    idx = pd.date_range(start, periods=len(values), freq="D")
    return pd.Series(values, index=idx, dtype=float)


N = _CORR_MIN_OVERLAP_DAYS  # 有效相关所需的最少重叠天数


def test_perfect_correlation_is_one():
    a = _series([0.1 * i for i in range(N)])
    b = 2 * a + 1  # 线性变换 → 相关系数恒为 1
    out = _build_ic_corr_matrix({1: a, 2: b}, [1, 2], ["A", "B"])
    assert out["matrix"][0][0] == 1.0
    assert out["matrix"][1][1] == 1.0
    assert out["matrix"][0][1] == 1.0
    assert out["matrix"][1][0] == 1.0
    assert out["overlap_counts"][0][1] == N
    assert out["ic_summary"]["1"]["n_days"] == N


def test_anti_correlation_is_minus_one():
    a = _series([0.1 * i for i in range(N)])
    b = -a
    out = _build_ic_corr_matrix({1: a, 2: b}, [1, 2], ["A", "B"])
    assert out["matrix"][0][1] == -1.0
    assert out["matrix"][1][0] == -1.0


def test_disjoint_dates_yield_nan():
    a = _series([0.1 * i for i in range(N)], start="2024-01-01")
    b = _series([0.1 * i for i in range(N)], start="2025-01-01")  # 完全不重叠
    out = _build_ic_corr_matrix({1: a, 2: b}, [1, 2], ["A", "B"])
    assert out["overlap_counts"][0][1] == 0
    assert out["matrix"][0][1] is None
    assert out["matrix"][1][0] is None
    # 对角线仍有效
    assert out["matrix"][0][0] == 1.0


def test_insufficient_overlap_is_nan_with_count():
    a = _series([0.1 * i for i in range(N + 10)], start="2024-01-01")  # 1月1日~1月30日
    b = _series([0.1 * i for i in range(10)], start="2024-01-21")  # 仅与 a 重叠 10 天 < N
    out = _build_ic_corr_matrix({1: a, 2: b}, [1, 2], ["A", "B"])
    assert out["overlap_counts"][0][1] == 10
    assert out["matrix"][0][1] is None


def test_missing_factor_row_is_none():
    a = _series([0.1 * i for i in range(N)])
    out = _build_ic_corr_matrix({1: a}, [1, 2], ["A", "B"])  # 因子 2 加载失败
    assert out["matrix"][0][1] is None
    assert out["matrix"][1][1] is None
    assert out["ic_summary"]["2"]["n_days"] == 0
    assert out["ic_summary"]["2"]["ic_mean"] is None


def test_zero_variance_factor_is_nan():
    a = _series([0.1 * i for i in range(N)])
    b = _series([0.05] * N)  # 常数序列，零方差
    out = _build_ic_corr_matrix({1: a, 2: b}, [1, 2], ["A", "B"])
    assert out["matrix"][0][1] is None
    assert out["ic_summary"]["2"]["icir"] is None


def test_ic_summary_stats():
    a = _series([0.1] * 10 + [0.3] * (N - 10))
    out = _build_ic_corr_matrix({7: a}, [7], ["G"])
    s = out["ic_summary"]["7"]
    assert s["name"] == "G"
    assert s["n_days"] == N
    assert s["ic_mean"] == pytest.approx(0.2, abs=1e-3)
    assert s["icir"] is not None


# ---------- API 参数校验（直接调用端点函数，mock qlib 可用性，无需 DB/qlib 数据） ----------


@pytest.fixture
def _qlib_ok(monkeypatch):
    from app.services.quant import qlib_init

    monkeypatch.setattr(qlib_init, "is_qlib_available", AsyncMock(return_value=True))


@pytest.mark.parametrize("bad_ids", ["abc", "1,,x", " "])
async def test_api_rejects_non_integer_ids(bad_ids, _qlib_ok):
    from app.api.factor_ext import factor_correlation_matrix_api
    from app.core.errors import AppError

    with pytest.raises(AppError) as ei:
        await factor_correlation_matrix_api(factor_ids=bad_ids)
    assert ei.value.code == "VALIDATION_ERROR"


async def test_api_rejects_more_than_20(_qlib_ok):
    from app.api.factor_ext import factor_correlation_matrix_api
    from app.core.errors import AppError

    ids = ",".join(str(i) for i in range(21))
    with pytest.raises(AppError) as ei:
        await factor_correlation_matrix_api(factor_ids=ids)
    assert ei.value.code == "VALIDATION_ERROR"


async def test_api_qlib_unavailable(monkeypatch):
    from app.services.quant import qlib_init
    from app.api.factor_ext import factor_correlation_matrix_api
    from app.core.errors import AppError

    monkeypatch.setattr(qlib_init, "is_qlib_available", AsyncMock(return_value=False))
    with pytest.raises(AppError) as ei:
        await factor_correlation_matrix_api(factor_ids="1,2")
    assert ei.value.code == "QLIB_NOT_AVAILABLE"
