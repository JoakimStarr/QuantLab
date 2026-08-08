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
    "ps_ttm", "pcf_ncf_ttm", "adjustflag", "change", "tradable", "factor",
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
    """stock_daily 16 列应被 bin 字段覆盖，change/tradable/factor 为预期衍生。"""
    result = check_fieldset()
    assert result["status"] == "ok"
    assert result["missing_in_bin"] == []
    assert set(result["derived_expected"]) <= {"change", "tradable", "factor"}


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


def test_calendar_diff_window_outside_not_a_gap():
    """早于数据起点的历史交易日历不判缺口（历次更大窗口回填累积的残留）。"""
    full = {f"2024-01-{i:02d}" for i in range(1, 11)}
    # 数据窗口内缺 2024-01-03（日历有、数据无 → 真缺口）
    stock_daily = full - {"2024-01-03"}
    day_txt = full
    # 日历还挂着 2000 年（早于数据起点）的历史交易日
    trade_cal = full | {"2000-06-01", "2000-06-02"}

    diff = _calendar_diff(day_txt, stock_daily, trade_cal)
    # 数据起点之后的缺失仍报缺口
    assert diff["pg_missing_dates"] == ["2024-01-03"]
    # 更早的日期只作信息报告，不算缺口
    assert diff["outside_calendar"] == ["2000-06-01", "2000-06-02"]
    assert diff["missing_in_day_txt"] == []


def test_calendar_diff_beyond_data_stays_gap():
    """比数据更新鲜的日历日（如尚未发布的交易日）仍保留为缺口候选，
    由 check_calendar 的发布时间点规则进一步区分，不在此抹掉。"""
    stock_daily = {f"2024-01-{i:02d}" for i in range(1, 11)}
    trade_cal = set(stock_daily) | {"2024-01-11"}
    diff = _calendar_diff(set(stock_daily), stock_daily, trade_cal)
    assert diff["pg_missing_dates"] == ["2024-01-11"]
    assert diff["outside_calendar"] == []


def test_calendar_diff_all_outside_when_no_data():
    """stock_daily 为空（从未回填）→ 全部日历都属历史信息，不算缺口。"""
    diff = _calendar_diff(set(), set(), {"2000-06-01", "2000-06-02"})
    assert diff["pg_missing_dates"] == []
    assert diff["outside_calendar"] == ["2000-06-01", "2000-06-02"]


# ----------------------------------------------- 今日未发布日期的过滤
def test_exclude_pending_today():
    from datetime import datetime
    from app.services.data.validation import _exclude_pending_today

    # 缺的是今天，且当前时间早于 baostock 发布时间点（18:00）→ 过滤（数据尚未发布）
    assert _exclude_pending_today(
        ["2026-08-05"], now=datetime(2026, 8, 5, 10, 20)
    ) == []
    # 缺的是今天，但已过发布时间点 → 保留（真缺口）
    assert _exclude_pending_today(
        ["2026-08-05"], now=datetime(2026, 8, 5, 20, 0)
    ) == ["2026-08-05"]
    # 缺的不是今天 → 不受影响
    assert _exclude_pending_today(
        ["2026-08-04"], now=datetime(2026, 8, 5, 10, 20)
    ) == ["2026-08-04"]
    # 空列表
    assert _exclude_pending_today([], now=datetime(2026, 8, 5, 10, 20)) == []


@pytest.mark.asyncio
async def test_check_calendar_excludes_pending_today_from_stock_daily(tmp_path, monkeypatch):
    """回归：day.txt 含今天但 stock_daily 未到 → 不计入 missing_in_stock_daily。

    baostock 回填会把"今天"写进 day.txt（日历对齐），但当日数据要收盘后发布。
    此前该日期在 missing_in_stock_daily 里被当 error，现在发布时间点前豁免。
    """
    import app.services.data.validation as val
    from datetime import datetime

    today = "2026-08-05"
    qlib_dir = tmp_path / "qlib"
    (qlib_dir / "calendars").mkdir(parents=True)
    with open(qlib_dir / "calendars" / "day.txt", "w") as f:
        f.write("2026-08-04\n" + today + "\n")

    from unittest.mock import AsyncMock, MagicMock

    class _Row:
        def __init__(self, v):
            self._v = v

        def __getitem__(self, i):
            return self  # r[0] 即该行自身

        def strftime(self, fmt):
            from datetime import date
            return date.fromisoformat(self._v).strftime(fmt)

    results = [
        [_Row("2026-08-04")],                # stock_daily distinct trade_date
        [_Row("2026-08-04"), _Row(today)],   # trade_calendar 交易日
    ]

    async def _fake_execute(stmt):
        r = MagicMock()
        r.__iter__ = lambda self: iter(results.pop(0))
        return r

    fake_session = AsyncMock()
    fake_session.execute = _fake_execute
    fake_session.__aenter__.return_value = fake_session
    fake_session.__aexit__.return_value = False

    monkeypatch.setattr(val, "async_session", lambda: fake_session)
    monkeypatch.setattr(val.settings.scheduler, "quant_data_update_time", "18:00")
    # 固定"当前时间"为 08-05 10:20（早于发布点）：_exclude_pending_today 默认取
    # 真实 now，这里包一层强制注入测试时间，验证 check_calendar 对 missing_in_stock_daily 也豁免
    real_exclude = val._exclude_pending_today
    monkeypatch.setattr(
        val, "_exclude_pending_today",
        lambda dates, now=None: real_exclude(dates, now=datetime(2026, 8, 5, 10, 20)),
    )

    result = await val.check_calendar(str(qlib_dir))
    counts = result["counts"]
    assert counts["missing_in_stock_daily"] == 0  # 今天被豁免
    assert result["status"] in ("ok", "warn")
    assert result["missing_in_stock_daily_samples"] == []


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
