"""数据修复模块测试（无需 DB）。

覆盖 _db_rows_to_df 映射、缺失交易日年数估算、从 PG 行重建单只股票 bin。
"""
import os
import struct
from datetime import date, timedelta

import numpy as np

from app.services.data.repair import (
    _compute_years_from_missing,
    _db_rows_to_df,
    _rebuild_one_stock,
    _recompute_targets,
)


def _sample_rows(n=5, code="SH600000"):
    rows = []
    for i in range(n):
        rows.append({
            "code": code,
            "trade_date": date(2024, 1, i + 1),
            "open": 10.0 + i, "high": 12.0 + i, "low": 9.0 + i, "close": 11.0 + i,
            "preclose": 10.5 + i, "volume": 100000.0, "amount": 1e6,
            "turn": 0.5, "tradestatus": 1, "pct_chg": 2.5 + i,
            "is_st": False, "pe_ttm": 10.0, "pb_mrq": 1.5,
            "ps_ttm": 2.0, "pcf_ncf_ttm": 3.0, "adjustflag": 1,
        })
    return rows


def test_db_rows_to_df_mapping():
    """stock_daily 行 → _build_out_df 输入：日期转 str、pctChg、isST。"""
    df = _db_rows_to_df(_sample_rows())
    assert "date" in df.columns
    assert df["date"].iloc[0] == "2024-01-01"
    assert "pctChg" in df.columns
    assert float(df["pctChg"].iloc[0]) == 2.5
    assert bool(df["isST"].iloc[0]) is False
    assert df["isST"].dtype == bool


def test_db_rows_to_df_empty():
    df = _db_rows_to_df([])
    assert df.empty


def test_compute_years_from_missing():
    """years 需保证回填窗口覆盖最早缺失交易日。"""
    years = _compute_years_from_missing(["2020-08-01"])
    assert years >= 1
    assert date.today() - timedelta(days=365 * years) <= date(2020, 8, 1)
    assert _compute_years_from_missing([]) == 1
    assert _compute_years_from_missing(["bad-date"]) == 1


def test_recompute_targets_reuses_first_validation():
    """日历未重建：直接复用首轮校验 repair_codes，不二次全量扫描。"""
    report = {
        "checks": {
            "fields": {"repair_codes": ["sz000001", "sh600000"]},
            "coverage": {"repair_codes": ["sz300001", "sh600000"]},
        }
    }
    targets = _recompute_targets(report, calendar_rebuilt=False,
                                 index_codes={"sh000001"}, code_range={})
    # 并集 + 去重排序；指数目录不进入目标
    assert targets == ["sh600000", "sz000001", "sz300001"]


def test_recompute_targets_no_repair_codes():
    """无差异时目标为空（不重建任何 bin）。"""
    report = {"checks": {"fields": {"repair_codes": []}, "coverage": {"repair_codes": []}}}
    assert _recompute_targets(report, False, set(), {}) == []


def test_recompute_targets_calendar_rebuilt_full_rebuild():
    """日历重建：全部股票（除指数）都是目标，无需扫描确认。"""
    report = {"checks": {"fields": {"repair_codes": []}, "coverage": {"repair_codes": []}}}
    code_range = {"sz000001": ["2020-01-01", "2026-01-01"],
                  "sh600000": ["2020-01-01", "2026-01-01"],
                  "sh000001": ["2020-01-01", "2026-01-01"]}
    targets = _recompute_targets(report, calendar_rebuilt=True,
                                 index_codes={"sh000001"}, code_range=code_range)
    assert sorted(targets) == ["sh600000", "sz000001"]


def test_rebuild_stock_bin_from_pg(tmp_path):
    """从 PG 行重建单只股票 bin：18 个字段文件齐全且长度与日历一致。"""
    base = tmp_path / "qlib"
    (base / "calendars").mkdir(parents=True)
    (base / "features" / "sh600000").mkdir(parents=True)
    calendar = [f"2024-01-{i:02d}" for i in range(1, 6)]
    with open(base / "calendars" / "day.txt", "w") as f:
        for d in calendar:
            f.write(d + "\n")

    _rebuild_one_stock("SH600000", _sample_rows(5), calendar, str(base))

    expected_size = 4 + 4 * 5
    for field in ["open", "high", "low", "close", "preclose", "volume", "amount",
                  "turn", "tradestatus", "pct_chg", "is_st", "pe_ttm", "pb_mrq",
                  "ps_ttm", "pcf_ncf_ttm", "adjustflag", "change", "tradable"]:
        p = base / "features" / "sh600000" / f"{field}.day.bin"
        assert p.exists(), f"{field}.day.bin 未生成"
        assert os.path.getsize(p) == expected_size, f"{field} 长度异常"

    # change = pct_chg/100
    close_path = base / "features" / "sh600000" / "close.day.bin"
    with open(close_path, "rb") as f:
        raw = np.fromfile(f, dtype="<f4")
    assert raw.size == 1 + 5
    # 有 5 个非 NaN 收盘价
    assert np.count_nonzero(np.isfinite(raw[1:])) == 5
