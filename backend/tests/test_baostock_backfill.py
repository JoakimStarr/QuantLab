# -*- coding: utf-8 -*-
"""baostock_backfill 去重/增量同步逻辑单元测试。

覆盖：
- _select_new_dates：跳过已下载日期，仅返回未下载日期（最新 → 最旧）
- _load_feature_ranges：stock_daily 为空时按 bin 长度推断每股数据区间
"""
import os
import struct

import pytest

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


async def test_pull_misc_data_default_skips():
    """默认（refresh_misc=False）不拉 stock_basic/stock_industry（用户要求关掉该步骤）。"""
    from unittest.mock import patch

    from app.services.data.baostock_backfill import _pull_misc_data

    with patch("app.services.data.baostock_backfill._fetch_all_sync",
               side_effect=AssertionError("默认不应拉取")) as m_fetch:
        df_basic, df_industry = await _pull_misc_data(False)

    assert df_basic is None and df_industry is None
    m_fetch.assert_not_called()


async def test_pull_misc_data_explicit_pulls():
    """refresh_misc=True 时才拉取基础资料/行业。"""
    from unittest.mock import patch

    import pandas as pd

    from app.services.data.baostock_backfill import _pull_misc_data

    basic_df = pd.DataFrame({"code": ["sh.600000"], "code_name": ["浦发银行"], "type": ["1"]})
    ind_df = pd.DataFrame({"code": ["sh.600000"], "code_name": ["浦发银行"],
                           "industry": ["银行"], "industryClassification": ["申万一级"]})

    def _fake_fetch(api_name, date_str):
        return {"query_stock_basic": basic_df.to_dict("records"),
                "query_stock_industry": ind_df.to_dict("records")}[api_name]

    with patch("app.services.data.baostock_backfill._fetch_all_sync",
               side_effect=_fake_fetch) as m_fetch:
        df_basic, df_industry = await _pull_misc_data(True)

    assert df_basic is not None and not df_basic.empty
    assert df_industry is not None and not df_industry.empty
    assert m_fetch.call_count == 2


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

    def _fake_flush(per_stock, global_calendar, qlib_dir, code_range, pg_rows, old_calendar=None, written_codes=None):
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

    def _fake_flush(per_stock, global_calendar, qlib_dir, code_range, pg_rows, old_calendar=None, written_codes=None):
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


async def test_run_backfill_downloads_multi_chunk_keeps_all_bin_data(tmp_path):
    """分块回填（>1 批）不丢数据：前几批写入的 bin 不会被后续批次清成 NaN。

    回归：旧实现每批都传回填前 old_calendar 映射旧值，第一批写入的新日期
    （位于旧日历长度之后）被映射为 -1 丢弃，最终 bin 只剩最后一批数据。
    """
    from unittest.mock import AsyncMock, patch

    import numpy as np
    import pandas as pd

    from app.services.data.baostock_backfill import _run_backfill_downloads
    from app.services.data.eod_incremental import _read_bin

    qlib_dir = str(tmp_path)
    dates = ["2024-01-19", "2024-01-18", "2024-01-17", "2024-01-16", "2024-01-15", "2024-01-12"]  # 最新→最旧（to_download）
    global_cal = sorted(dates)  # 真实调用方 run_baostock_backfill 传入的 global_calendar 是升序
    dfs = {}
    for i, d in enumerate(dates):
        dfs[d] = pd.DataFrame({
            "qlib_code_lower": ["sh600000"],
            "date": [d],
            "open": [10.0 + i], "high": [11.0 + i], "low": [9.0 + i],
            "close": [10.5 + i], "preclose": [10.0 + i],
            "volume": [1000.0], "amount": [10000.0],
            "turn": [1.0], "tradestatus": [1], "pctChg": [1.0], "isST": [False],
            "peTTM": [10.0], "pbMRQ": [1.0], "psTTM": [2.0], "pcfNcfTTM": [3.0],
            "adjustflag": [3],
        })

    with patch(
        "app.services.data.baostock_backfill.fetch_daily_all_a_stock_sync",
        side_effect=lambda d: dfs[d],
    ), patch(
        "app.services.data.baostock_backfill._normalize_daily",
        side_effect=lambda df: df,
    ), patch(
        "app.services.data.baostock_backfill._insert_stock_daily",
        new=AsyncMock(side_effect=lambda rows: {r["trade_date"].strftime("%Y-%m-%d") for r in rows}),
    ):
        n = await _run_backfill_downloads(
            dates, global_cal, qlib_dir, {},
            chunk_days=2, queue_max=4,
            written_days=set(), old_calendar=[],
        )

    assert n == 3  # 3 个批次 × 同一只股票，每批成功计入一次
    close, start = _read_bin(os.path.join(qlib_dir, "features", "sh600000", "close.day.bin"))
    assert start == 0
    assert len(close) == len(global_cal)
    assert not np.isnan(close).any(), "分块回填后 bin 不应出现 NaN（前几批数据被丢弃）"
    # 值按日历升序写入：最早日期(01-12, i=5)在最前，最新日期(01-19, i=0)在最后
    assert close.tolist() == pytest.approx([10.5 + i for i in range(5, -1, -1)])


# ---------- 动态成分缓存 / 空采样熔断（2026-08 优化） ----------

def test_dynamic_cache_fresh_within_limits():
    """7 天内构建且日历末端推进 ≤7 个交易日 → 新鲜。"""
    from datetime import datetime
    from app.services.data.baostock_backfill import _dynamic_cache_fresh
    cal = [f"2026-01-{d:02d}" for d in range(1, 21)]
    cache = {"built_at": datetime.now().isoformat(timespec="seconds"),
             "cal_end": cal[12]}  # 末端推进 7 个交易日
    assert _dynamic_cache_fresh(cache, cal) is True


def test_dynamic_cache_stale_after_7_days():
    """构建超 7 天 → 过期。"""
    from datetime import datetime, timedelta
    from app.services.data.baostock_backfill import _dynamic_cache_fresh
    cal = [f"2026-01-{d:02d}" for d in range(1, 21)]
    cache = {"built_at": (datetime.now() - timedelta(days=8)).isoformat(timespec="seconds"),
             "cal_end": cal[-1]}
    assert _dynamic_cache_fresh(cache, cal) is False


def test_dynamic_cache_stale_when_cal_advanced_over_7():
    """日历末端推进 >7 个交易日 → 过期（成分调整需及时刷新）。"""
    from datetime import datetime
    from app.services.data.baostock_backfill import _dynamic_cache_fresh
    cal = [f"2026-01-{d:02d}" for d in range(1, 21)]
    cache = {"built_at": datetime.now().isoformat(timespec="seconds"),
             "cal_end": cal[10]}  # 末端推进 9 个交易日
    assert _dynamic_cache_fresh(cache, cal) is False


def test_dynamic_cache_fresh_when_cal_not_advanced():
    """日历末端未推进（无新交易日的全量同步）→ 新鲜。"""
    from datetime import datetime
    from app.services.data.baostock_backfill import _dynamic_cache_fresh
    cal = [f"2026-01-{d:02d}" for d in range(1, 21)]
    cache = {"built_at": datetime.now().isoformat(timespec="seconds"),
             "cal_end": cal[-1]}
    assert _dynamic_cache_fresh(cache, cal) is True


def test_rebuild_dynamic_instruments_cache_hit(tmp_path):
    """缓存新鲜时直接复用，不发起任何成分采样请求。"""
    from datetime import datetime
    from unittest.mock import patch
    from app.services.data import baostock_backfill as bb
    cal = [f"2026-01-{d:02d}" for d in range(1, 21)]
    cache = {"built_at": datetime.now().isoformat(timespec="seconds"),
             "cal_end": cal[-1], "counts": {"csi300": 300, "csi500": 500}}
    with patch.object(bb, "_load_dynamic_cache", return_value=cache), \
         patch.object(bb, "_fetch_membership_history") as m_fetch:
        counts = bb._rebuild_dynamic_instruments(str(tmp_path), cal)
    assert counts == {"csi300": 300, "csi500": 500}
    assert m_fetch.call_count == 0  # 0 次采样请求（省几十次串行 baostock 调用）


def test_fetch_membership_history_empty_circuit_breaker():
    """连续 3 个空采样点即中止，剩余采样点不再请求（省配额）。"""
    from unittest.mock import patch
    from app.services.data import baostock_backfill as bb
    samples = [f"2024-{m:02d}-01" for m in range(1, 13)]  # 12 个采样点
    with patch.object(bb, "_fetch_all_sync", return_value=[]) as m_fetch, \
         patch("app.services.data.baostock_client._ensure_login"):
        spans = bb._fetch_membership_history("query_hs300_stocks", samples)
    assert m_fetch.call_count == 3  # 第 3 个空采样点熔断，剩余 9 个跳过
    assert spans == {}
