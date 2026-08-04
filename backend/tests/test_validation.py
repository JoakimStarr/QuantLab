"""数据校验模块测试（无需 DB）。

覆盖 check_fields / check_fieldset / _calendar_diff / _compute_range_mismatch。
"""
import os
import struct

import numpy as np
import pytest

from app.services.data.validation import (
    _calendar_diff,
    _compute_range_mismatch,
    check_fieldset,
    check_fields,
)


def _write_bin(path, values):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(struct.pack("<f", 0.0))  # start_index = 0
        np.asarray(values, dtype="<f4").tofile(f)


ALL_FIELDS = [
    "open", "high", "low", "close", "preclose", "volume", "amount",
    "turn", "tradestatus", "pct_chg", "is_st", "pe_ttm", "pb_mrq",
    "ps_ttm", "pcf_ncf_ttm", "adjustflag", "change", "tradable",
]


def _make_qlib_dir(tmp_path, calendar_days=10, stocks=None, fields=None):
    """建临时 qlib 目录：10 天日历 + 若干股票 × 指定字段（长度=日历天数）。"""
    base = tmp_path / "qlib"
    (base / "calendars").mkdir(parents=True)
    (base / "features").mkdir(parents=True)
    dates = [f"2024-01-{i:02d}" for i in range(1, calendar_days + 1)]
    with open(base / "calendars" / "day.txt", "w") as f:
        for d in dates:
            f.write(d + "\n")
    for s in (stocks or ["sh600000"]):
        sdir = base / "features" / s
        sdir.mkdir(parents=True)
        for fld in (fields or ALL_FIELDS):
            _write_bin(sdir / f"{fld}.day.bin", np.abs(np.random.randn(calendar_days)) + 50)
    return base


# ---------------------------------------------------------------- fieldset
def test_fieldset_bin_superset():
    """stock_daily 16 列应被 18 个 bin 字段覆盖，change/tradable 为预期衍生。"""
    result = check_fieldset()
    assert result["status"] == "ok"
    assert result["missing_in_bin"] == []
    assert set(result["derived_expected"]) <= {"change", "tradable"}


# ------------------------------------------------------------- calendar diff
def test_calendar_diff_basic():
    day_txt = {f"2024-01-{i:02d}" for i in range(1, 11)}
    stock_daily = {f"2024-01-{i:02d}" for i in range(1, 10)} | {"2024-02-01"}
    trade_cal = stock_daily | {"2024-02-02"}

    diff = _calendar_diff(day_txt, stock_daily, trade_cal)
    assert diff["missing_in_day_txt"] == ["2024-02-01"]  # 库有、day.txt 无
    assert diff["missing_in_stock_daily"] == ["2024-01-10"]  # day.txt 有、库无
    assert diff["pg_missing_dates"] == ["2024-02-02"]  # 需 baostock 补拉的交易日


def test_calendar_diff_empty():
    diff = _calendar_diff(set(), set(), set())
    assert diff["missing_in_day_txt"] == []
    assert diff["missing_in_stock_daily"] == []
    assert diff["pg_missing_dates"] == []


# ------------------------------------------------------------------- fields
def test_check_fields_ok(tmp_path):
    base = _make_qlib_dir(tmp_path)
    result = check_fields(str(base), [f"2024-01-{i:02d}" for i in range(1, 11)])
    assert result["status"] == "ok"
    assert result["stocks_checked"] == 1
    assert result["missing_field_files"] == 0
    assert result["bad_size_stocks"] == 0
    assert result["suspicious_bin_stocks"] == 0
    assert result["repair_codes"] == []


def test_check_fields_missing_and_bad_size(tmp_path):
    base = _make_qlib_dir(tmp_path, stocks=["sh600000", "sz000001", "sz300001"])
    # sz000001: 缺 close.day.bin
    os.remove(base / "features" / "sz000001" / "close.day.bin")
    # sz300001: close 文件长度错误（只写 5 个点，预期 10 个）
    _write_bin(base / "features" / "sz300001" / "close.day.bin", [1, 2, 3, 4, 5])

    result = check_fields(str(base), [f"2024-01-{i:02d}" for i in range(1, 11)])
    assert result["status"] == "error"
    assert result["stocks_checked"] == 3
    assert result["missing_field_files"] == 1
    assert "sz000001: close" in result["missing_field_samples"]
    assert result["bad_size_stocks"] == 1
    assert "sz300001" in result["bad_size_samples"][0]
    assert "sz000001" in result["repair_codes"]
    assert "sz300001" in result["repair_codes"]


def test_check_fields_detects_duplicated_bin(tmp_path):
    """data[:5]==data[-5:] 首尾重复 = 已知写入 bug 特征。"""
    base = _make_qlib_dir(tmp_path, stocks=["sh600000", "sz000001"])
    # 把 sz000001 的 close 改为首尾重复但长度正确
    _write_bin(base / "features" / "sz000001" / "close.day.bin",
               [10, 11, 12, 13, 14, 10, 11, 12, 13, 14])
    result = check_fields(str(base), [f"2024-01-{i:02d}" for i in range(1, 11)])
    assert result["suspicious_bin_stocks"] == 1
    assert "sz000001" in result["repair_codes"]


def test_check_fields_detects_all_nan(tmp_path):
    """close 全 NaN 且长度正确 → 疑似损坏并加入修复目标。"""
    base = _make_qlib_dir(tmp_path, stocks=["sh600000", "sz000001"])
    _write_bin(base / "features" / "sz000001" / "close.day.bin", [np.nan] * 10)
    result = check_fields(str(base), [f"2024-01-{i:02d}" for i in range(1, 11)])
    assert result["suspicious_bin_stocks"] == 1
    assert "sz000001" in result["repair_codes"]


def test_check_fields_empty_features(tmp_path):
    base = tmp_path / "empty"
    base.mkdir()
    result = check_fields(str(base), [])
    assert result["status"] == "warn"
    assert result["stocks_checked"] == 0


# ------------------------------------------------------------ range mismatch
def test_compute_range_mismatch(tmp_path):
    calendar = [f"2024-01-{i:02d}" for i in range(1, 11)]
    base = _make_qlib_dir(tmp_path, stocks=["sh600000", "sz000001"])
    # sz000001 的 close bin 只覆盖前 5 天 → bin 末日期 2024-01-05 < DB max
    _write_bin(base / "features" / "sz000001" / "close.day.bin", [1, 2, 3, 4, 5])
    bin_dirs = {"sh600000", "sz000001"}

    db_ranges = {
        "sh600000": ["2024-01-01", "2024-01-10"],
        "sz000001": ["2024-01-01", "2024-01-10"],
    }
    mismatch = _compute_range_mismatch(str(base), calendar, db_ranges, bin_dirs)
    assert mismatch == ["sz000001"]


def test_compute_range_mismatch_no_db(tmp_path):
    base = _make_qlib_dir(tmp_path)
    assert _compute_range_mismatch(str(base), [], {}, set()) == []
