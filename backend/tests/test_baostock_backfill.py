# -*- coding: utf-8 -*-
"""baostock_backfill 去重/增量同步逻辑单元测试。

覆盖：
- _select_new_dates：跳过已下载日期，仅返回未下载日期（最新 → 最旧）
- _load_feature_ranges：stock_daily 为空时按 bin 长度推断每股数据区间
"""
import struct

from app.services.data.baostock_backfill import (
    _load_feature_ranges,
    _select_new_dates,
)


def test_select_new_dates_skips_downloaded():
    trade_dates = ["2024-01-15", "2024-01-16", "2024-01-17", "2024-01-18"]
    downloaded = {"2024-01-16", "2024-01-17"}
    result = _select_new_dates(trade_dates, downloaded)
    # 未下载：01-18（最新）、01-15（最旧），最新在前
    assert result == ["2024-01-18", "2024-01-15"]


def test_select_new_dates_empty_when_all_downloaded():
    trade_dates = ["2024-01-15", "2024-01-16", "2024-01-17"]
    result = _select_new_dates(trade_dates, set(trade_dates))
    assert result == []


def test_select_new_dates_none_downloaded():
    trade_dates = ["2024-01-15", "2024-01-16", "2024-01-17"]
    result = _select_new_dates(trade_dates, set())
    assert result == ["2024-01-17", "2024-01-16", "2024-01-15"]


def test_select_new_dates_order_newest_first():
    trade_dates = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]
    result = _select_new_dates(trade_dates, {"2024-01-02"})
    assert result == ["2024-01-04", "2024-01-03", "2024-01-01"]


def _write_bin_file(path, n_values):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(struct.pack("<f", 0.0))  # start_index 头
        f.write(b"\x00" * (n_values * 4))  # n 个 float32


def test_load_feature_ranges_from_bin_length(tmp_path):
    """stock_daily 为空时，按 features bin 长度推断每股数据区间。"""
    calendar = ["2024-01-15", "2024-01-16", "2024-01-17", "2024-01-18"]
    feat = tmp_path / "features"
    # sh600000 覆盖全部 4 个交易日 → [2024-01-15, 2024-01-18]
    _write_bin_file(feat / "sh600000" / "close.day.bin", 4)
    # sz000001 仅 2 个交易日 → [2024-01-15, 2024-01-16]
    _write_bin_file(feat / "sz000001" / "close.day.bin", 2)
    # 无 close.bin 的目录（非股票）应被忽略
    (feat / "emptydir").mkdir(parents=True)

    ranges = _load_feature_ranges(str(tmp_path), calendar)
    assert ranges == {
        "sh600000": ["2024-01-15", "2024-01-18"],
        "sz000001": ["2024-01-15", "2024-01-16"],
    }


def test_load_feature_ranges_missing_calendar_returns_empty(tmp_path):
    assert _load_feature_ranges(str(tmp_path), []) == {}


async def test_run_backfill_downloads_pipeline():
    """流水线：串行拉取→后台写盘，所有日期都被消费并落库。"""
    from unittest.mock import AsyncMock, patch

    import pandas as pd

    from app.services.data.baostock_backfill import _run_backfill_downloads

    dates = ["2024-01-16", "2024-01-15"]  # 最新 → 最旧
    dfs = {}
    for d in dates:
        dfs[d] = pd.DataFrame({
            "qlib_code_lower": ["sh600000"],
            "date": [d],
            "open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5],
            "preclose": [10.0], "volume": [1000.0], "amount": [10000.0],
            "turn": [1.0], "tradestatus": [1], "pctChg": [5.0], "isST": [False],
            "peTTM": [10.0], "pbMRQ": [1.0], "psTTM": [2.0], "pcfNcfTTM": [3.0],
            "adjustflag": [3],
        })

    flushed = []

    def _fake_flush(per_stock, global_calendar, qlib_dir, code_range, pg_rows):
        flushed.append(len(per_stock))
        pg_rows.extend([1] * len(per_stock))
        return len(per_stock)

    with patch(
        "app.services.data.baostock_backfill.fetch_daily_all_a_stock_sync",
        side_effect=lambda d: dfs[d],
    ), patch(
        "app.services.data.baostock_backfill._normalize_daily",
        side_effect=lambda df: df,
    ), patch(
        "app.services.data.baostock_backfill._insert_stock_daily",
        new=AsyncMock(side_effect=lambda rows: None),
    ) as mock_insert, patch(
        "app.services.data.baostock_backfill._flush_chunk",
        side_effect=_fake_flush,
    ) as mock_flush:
        code_range = {}
        n = await _run_backfill_downloads(
            dates, ["2024-01-15", "2024-01-16"], "qlib", code_range,
            chunk_days=1, queue_max=2,
        )

    assert n == 2
    assert flushed == [1, 1]
    assert mock_insert.await_count == 2
    assert mock_flush.call_count == 2


async def test_run_backfill_downloads_syncs_calendar(tmp_path):
    """每批数据读写成功后，立即把新日期回填 day.txt，与已下载日期并集排序。"""
    from datetime import date
    from unittest.mock import AsyncMock, patch

    import pandas as pd

    from app.services.data.baostock_backfill import _run_backfill_downloads

    qlib_dir = str(tmp_path)
    dates = ["2024-01-16", "2024-01-15"]  # 最新 → 最旧
    dfs = {}
    for d in dates:
        dfs[d] = pd.DataFrame({
            "qlib_code_lower": ["sh600000"],
            "date": [d],
            "open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5],
            "preclose": [10.0], "volume": [1000.0], "amount": [10000.0],
            "turn": [1.0], "tradestatus": [1], "pctChg": [5.0], "isST": [False],
            "peTTM": [10.0], "pbMRQ": [1.0], "psTTM": [2.0], "pcfNcfTTM": [3.0],
            "adjustflag": [3],
        })

    def _fake_flush(per_stock, global_calendar, qlib_dir, code_range, pg_rows):
        for code_lower, rows in per_stock.items():
            pg_rows.extend({
                "code": code_lower.upper(),
                "trade_date": date.fromisoformat(r["date"]),
            } for r in rows)
        return len(per_stock)

    def _fake_insert(rows):
        return {r["trade_date"].strftime("%Y-%m-%d") for r in rows}

    with patch(
        "app.services.data.baostock_backfill.fetch_daily_all_a_stock_sync",
        side_effect=lambda d: dfs[d],
    ), patch(
        "app.services.data.baostock_backfill._normalize_daily",
        side_effect=lambda df: df,
    ), patch(
        "app.services.data.baostock_backfill._insert_stock_daily",
        new=AsyncMock(side_effect=_fake_insert),
    ), patch(
        "app.services.data.baostock_backfill._flush_chunk",
        side_effect=_fake_flush,
    ):
        n = await _run_backfill_downloads(
            dates, ["2024-01-15", "2024-01-16"], qlib_dir, {},
            chunk_days=1, queue_max=2, written_days={"2024-01-10"},
        )

    assert n == 2
    cal = (tmp_path / "calendars" / "day.txt").read_text().splitlines()
    # 种子日期 + 两批新增日期，升序且与数据库同步
    assert cal == ["2024-01-10", "2024-01-15", "2024-01-16"]
