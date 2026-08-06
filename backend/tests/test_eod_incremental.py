# -*- coding: utf-8 -*-
"""eod_incremental 单元测试。

覆盖：
- _get_limit_pct 板块涨跌停比例
- _compute_tradable ST 5% 涨跌停 mask 修复（核心 bug 修复点）
- baostock 主源写 bin（mock 契约全列，验证 ST mask 流入 bin）
- baostock 空数据/失败 → akshare 回退分发
- source='akshare' 显式跳过 baostock
"""
from unittest.mock import AsyncMock, patch

import numpy as np
import pandas as pd
import pytest

from app.services.data import eod_incremental as eod


# ---------- _get_limit_pct ----------

def test_get_limit_pct_main_board():
    assert eod._get_limit_pct("sz000001") == 0.10
    assert eod._get_limit_pct("sh600000") == 0.10


def test_get_limit_pct_kc_cy():
    assert eod._get_limit_pct("sh688001") == 0.20
    assert eod._get_limit_pct("sz300001") == 0.20


def test_get_limit_pct_bj():
    assert eod._get_limit_pct("bj430047") == 0.30


# ---------- _compute_tradable ST 5% mask ----------

def test_st_5pct_limit_up_marked_untradable():
    close = pd.Series([10.0, 10.5, 11.0, 11.55])
    pct = pd.Series([0.0, 5.0, 4.76, 5.0])  # 5% 涨停
    is_st = pd.Series([False, True, True, True])
    t = eod._compute_tradable(close, pct, code="sz000001", is_st=is_st)
    assert t.tolist() == [1.0, 0.0, 1.0, 0.0]
    assert t.iloc[1] == 0.0  # ST 5% 涨停不可交易
    assert t.iloc[3] == 0.0  # ST 5% 涨停不可交易
    assert t.iloc[2] == 1.0  # ST 4.76% 未触及 5%，可交易


def test_st_5pct_limit_down_marked_untradable():
    t = eod._compute_tradable(
        pd.Series([10.0, 9.5]), pd.Series([0.0, -5.0]),
        code="sz000001", is_st=pd.Series([False, True]),
    )
    assert t.iloc[1] == 0.0  # ST -5% 跌停不可交易


def test_non_st_uses_board_threshold():
    t = eod._compute_tradable(
        pd.Series([10.0, 11.0, 12.0]),
        pd.Series([0.0, 5.0, 10.0]),
        code="sz000001", is_st=None,
    )
    assert t.tolist() == [1.0, 1.0, 0.0]  # 10% 涨停


def test_non_st_kc_20pct():
    t = eod._compute_tradable(
        pd.Series([10.0, 12.0]), pd.Series([0.0, 20.0]),
        code="sh688001", is_st=None,
    )
    assert t.iloc[1] == 0.0


def test_is_st_none_backward_compat():
    # akshare 路径 is_st=None，9.90% 未触及主板10% → 可交易
    t = eod._compute_tradable(
        pd.Series([10.0, 10.99]), pd.Series([0.0, 9.90]),
        code="sz000001", is_st=None,
    )
    assert t.iloc[1] == 1.0


# ---------- _sync_stock_bin 日历对齐修复 ----------

def test_sync_bin_new_file_aligned_to_calendar(tmp_path):
    """新 bin 写入：数据数组长度必须等于全局日历长度，start_index=0。"""
    feat = tmp_path / "features" / "sh600000"
    feat.mkdir(parents=True)
    cal = ["2024-01-15", "2024-01-16", "2024-01-17"]
    df = pd.DataFrame({"date": ["2024-01-16", "2024-01-17"], "close": [10.0, 11.0]})
    eod._sync_stock_bin(str(feat), df, cal, ["close"], overwrite=True)

    values, start = eod._read_bin(str(feat / "close.day.bin"))
    assert start == 0
    assert len(values) == len(cal) == 3
    assert np.isnan(values[0])          # 01-15 无数据
    assert values[1] == 10.0            # 01-16
    assert values[2] == 11.0            # 01-17


def test_sync_bin_calendar_extend_keeps_old_data(tmp_path):
    """日历向后扩展：旧数据按日期映射保留，不丢失、不错位。"""
    feat = tmp_path / "features" / "sh600000"
    feat.mkdir(parents=True)
    cal1 = ["2024-01-15", "2024-01-16", "2024-01-17"]
    df1 = pd.DataFrame({"date": ["2024-01-15", "2024-01-16", "2024-01-17"],
                        "close": [10.0, 11.0, 12.0]})
    eod._sync_stock_bin(str(feat), df1, cal1, ["close"], overwrite=True)

    # 日历扩展两个新交易日
    cal2 = ["2024-01-15", "2024-01-16", "2024-01-17", "2024-01-18", "2024-01-19"]
    df2 = pd.DataFrame({"date": ["2024-01-18", "2024-01-19"], "close": [13.0, 14.0]})
    eod._sync_stock_bin(str(feat), df2, cal2, ["close"], overwrite=True)

    values, start = eod._read_bin(str(feat / "close.day.bin"))
    assert start == 0
    assert len(values) == 5
    assert values.tolist() == [10.0, 11.0, 12.0, 13.0, 14.0]


def test_sync_bin_mismatched_old_bin_rebuilt(tmp_path, caplog):
    """旧 bin 长度超出当前日历（日历被缩短过）→ 丢弃重建，不产生错位数据。"""
    import logging
    feat = tmp_path / "features" / "sh600000"
    feat.mkdir(parents=True)
    # 先用长日历写入（模拟历史完整日历）
    cal_long = ["2024-01-15", "2024-01-16", "2024-01-17", "2024-01-18", "2024-01-19"]
    df_long = pd.DataFrame({"date": cal_long, "close": [10.0, 11.0, 12.0, 13.0, 14.0]})
    eod._sync_stock_bin(str(feat), df_long, cal_long, ["close"], overwrite=True)

    # 传入更短日历（模拟日历被意外覆盖/缩短）→ 旧 bin 超界，应丢弃重建
    cal_short = ["2024-01-18", "2024-01-19"]
    df_short = pd.DataFrame({"date": ["2024-01-19"], "close": [99.0]})
    with caplog.at_level(logging.WARNING, logger="app.services.data.eod_incremental"):
        eod._sync_stock_bin(str(feat), df_short, cal_short, ["close"], overwrite=True)

    values, start = eod._read_bin(str(feat / "close.day.bin"))
    assert start == 0
    assert len(values) == 2
    assert np.isnan(values[0])          # 01-18 无新数据 → NaN
    assert values[1] == 99.0            # 01-19 新数据
    # 确认触发了丢弃重建警告
    assert any("不对齐" in r.message for r in caplog.records)


def test_st_overrides_board_threshold():
    # 同一只主板股，非ST日10%才停，ST日5%即停
    t = eod._compute_tradable(
        pd.Series([10.0, 10.5, 11.0]),
        pd.Series([0.0, 5.0, 10.0]),
        code="sz000001",
        is_st=pd.Series([False, True, False]),
    )
    assert t.tolist() == [1.0, 0.0, 0.0]  # ST日5%停，非ST日10%停


# ---------- baostock 主源 / akshare 回退分发 ----------

@pytest.fixture
def tmp_qlib(tmp_path):
    """临时 qlib 目录：calendars/day.txt + instruments/all.txt。"""
    base = tmp_path / "qlib"
    (base / "calendars").mkdir(parents=True)
    (base / "instruments").mkdir(parents=True)
    (base / "features").mkdir(parents=True)
    dates = ["2024-01-15", "2024-01-16", "2024-01-17"]
    with open(base / "calendars" / "day.txt", "w") as f:
        f.write("\n".join(dates) + "\n")
    with open(base / "instruments" / "all.txt", "w") as f:
        f.write("sh600000\t2024-01-01\t2024-12-31\n")
        f.write("sz000001\t2024-01-01\t2024-12-31\n")
    return base, dates


def _mock_baostock_full(date):
    """模拟 Step1 真实实现：返回契约全列（含 OHLCV/pctChg/isST）。

    sz000001 为 ST 且 pctChg=5%（触及 ST 5% 涨停）；sh600000 非 ST 且 pctChg=2%。
    """
    return pd.DataFrame({
        "date": [date, date],
        "code": ["sh.600000", "sz.000001"],
        "open": [10.0, 20.0],
        "high": [10.5, 21.0],
        "low": [9.8, 19.8],
        "close": [10.2, 21.0],
        "preclose": [10.0, 20.0],
        "volume": [100000.0, 200000.0],
        "amount": [1020000.0, 4200000.0],
        "adjustflag": ["3", "3"],
        "turn": [1.0, 2.0],
        "tradestatus": ["1", "1"],
        "pctChg": [2.0, 5.0],
        "peTTM": [15.0, 30.0],
        "pbMRQ": [1.5, 3.0],
        "psTTM": [5.0, 8.0],
        "pcfNcfTTM": [10.0, 20.0],
        "isST": ["0", "1"],
    })


def test_baostock_writes_bin_and_st_mask(tmp_qlib):
    """baostock 主源：mock 全列数据，验证写 bin + ST 5% mask 流入 tradable.bin。"""
    base, old_dates = tmp_qlib
    new_date = "2024-01-18"
    with patch("app.services.data.baostock_client.fetch_daily_all_a_stock_sync",
               side_effect=_mock_baostock_full):
        r = eod.incremental_sync_eod_baostock(
            [new_date], ["sh600000", "sz000001"], str(base),
            old_calendar=old_dates, overwrite=True, universe="all",
        )
    assert r["ok"]
    assert r["source"] == "baostock"
    assert r["success"] == 2
    assert r["failed"] == 0
    assert new_date in r["new_dates"]

    # pg_rows：EOD 落库 stock_daily（repair 以 PG 为权威，不落库会丢 EOD 数据）
    assert len(r["pg_rows"]) == 2
    assert r["pg_rows"][0]["code"] == "SH600000"
    assert r["pg_rows"][0]["is_st"] is False
    assert r["pg_rows"][0]["pct_chg"] == 2.0
    assert r["pg_rows"][0]["trade_date"] == new_date
    assert r["pg_rows"][1]["code"] == "SZ000001"
    assert r["pg_rows"][1]["is_st"] is True
    assert r["pg_rows"][1]["pct_chg"] == 5.0

    # sz000001 (ST, 5%) → tradable=0.0；sh600000 (非ST, 2%) → tradable=1.0
    sz_vals, _ = eod._read_bin(str(base / "features" / "sz000001" / "tradable.day.bin"))
    sh_vals, _ = eod._read_bin(str(base / "features" / "sh600000" / "tradable.day.bin"))
    # 新日期是合并日历最后一天
    assert sz_vals[-1] == 0.0, "ST 5% 涨停日 tradable 应为 0"
    assert sh_vals[-1] == 1.0, "非 ST 2% 日 tradable 应为 1"
    # close 也应写入（复权对齐：无旧 bin，ratio 不生效，原值写入）
    sz_close, _ = eod._read_bin(str(base / "features" / "sz000001" / "close.day.bin"))
    assert sz_close[-1] == pytest.approx(21.0, abs=1e-4)


def test_baostock_skips_non_trading_day(tmp_qlib):
    """baostock 返回空 DataFrame（非交易日/桩）→ 跳过，success=0，ok=True。"""
    base, old_dates = tmp_qlib
    with patch("app.services.data.baostock_client.fetch_daily_all_a_stock_sync",
               return_value=pd.DataFrame(columns=["date", "code"])):
        r = eod.incremental_sync_eod_baostock(
            ["2024-01-20"], ["sh600000"], str(base),
            old_calendar=old_dates, overwrite=False, universe="all",
        )
    assert r["ok"] is True
    assert r["success"] == 0
    assert r["dates"] == []


async def test_baostock_empty_falls_back_to_akshare(tmp_qlib):
    """baostock 返回空数据(success=0) → 回退 akshare 路径。"""
    base, _ = tmp_qlib
    sentinel = {"ok": True, "source": "akshare", "fallback": True}
    with patch("app.services.data.baostock_client.fetch_daily_all_a_stock_sync",
               return_value=pd.DataFrame(columns=["date", "code"])), \
         patch.object(eod, "_incremental_sync_eod_akshare",
                      new=AsyncMock(return_value=sentinel)) as mock_ak:
        r = await eod.incremental_sync_eod(
            universe="all", days=1, provider_uri=str(base),
            overwrite=False, source="baostock",
        )
    assert r == sentinel
    mock_ak.assert_awaited_once()


async def test_baostock_import_error_falls_back_to_akshare(tmp_qlib):
    """baostock_client 导入失败 → 回退 akshare。"""
    base, _ = tmp_qlib
    sentinel = {"ok": True, "source": "akshare"}
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name == "app.services.data.baostock_client":
            raise ImportError("simulated baostock_client missing")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import), \
         patch.object(eod, "_incremental_sync_eod_akshare",
                      new=AsyncMock(return_value=sentinel)) as mock_ak:
        r = await eod.incremental_sync_eod(
            universe="all", days=1, provider_uri=str(base), source="baostock",
        )
    assert r == sentinel
    mock_ak.assert_awaited_once()


async def test_source_akshare_skips_baostock(tmp_qlib):
    """source='akshare' 显式跳过 baostock，直接走 akshare。"""
    base, _ = tmp_qlib
    sentinel = {"ok": True, "source": "akshare"}
    with patch.object(eod, "incremental_sync_eod_baostock") as mock_bs, \
         patch.object(eod, "_incremental_sync_eod_akshare",
                      new=AsyncMock(return_value=sentinel)):
        r = await eod.incremental_sync_eod(
            universe="all", days=1, provider_uri=str(base), source="akshare",
        )
    assert r == sentinel
    mock_bs.assert_not_called()


async def test_baostock_partial_success_no_fallback(tmp_qlib):
    """baostock 取到数据(success>0) → 不回退，直接返回 baostock 结果。"""
    base, old_dates = tmp_qlib
    with patch("app.services.data.baostock_client.fetch_daily_all_a_stock_sync",
               side_effect=_mock_baostock_full), \
         patch.object(eod, "_incremental_sync_eod_akshare",
                      new=AsyncMock()) as mock_ak, \
         patch.object(eod, "_insert_pg_rows", new=AsyncMock()):
        r = await eod.incremental_sync_eod(
            universe="all", days=1, provider_uri=str(base),
            overwrite=True, source="baostock",
        )
    assert r["ok"]
    assert r["source"] == "baostock"
    assert r["success"] > 0
    mock_ak.assert_not_awaited()


async def test_baostock_success_writes_pg_rows(tmp_qlib):
    """baostock 主源成功 → pg_rows 经 _insert_pg_rows 落库 stock_daily。"""
    base, _ = tmp_qlib
    with patch("app.services.data.baostock_client.fetch_daily_all_a_stock_sync",
               side_effect=_mock_baostock_full), \
         patch.object(eod, "_insert_pg_rows", new=AsyncMock()) as mock_insert:
        r = await eod.incremental_sync_eod(
            universe="all", days=1, provider_uri=str(base),
            overwrite=True, source="baostock",
        )
    assert r["ok"]
    mock_insert.assert_awaited_once()
    rows = mock_insert.await_args.args[0]
    # 候选窗口内每个工作日都会取到 mock 数据 → 2 股 × N 天
    assert len(rows) >= 2
    assert {row["code"] for row in rows} == {"SH600000", "SZ000001"}
    assert all(len(row["trade_date"]) == 10 for row in rows)


async def test_akshare_writes_pg_rows(tmp_qlib):
    """akshare 兜底路径：新日期数据落库 stock_daily（仅新日期）。"""
    base, old_dates = tmp_qlib

    def _fake_fetch(qlib_code, start_str, end_str):
        return pd.DataFrame({
            "date": ["2024-01-18", "2024-01-19"],
            "open": [10.0, 11.0],
            "high": [10.5, 11.5],
            "low": [9.8, 10.8],
            "close": [10.2, 11.2],
            "volume": [100000.0, 110000.0],
            "pct_change": [2.0, 9.8],
        })

    with patch.object(eod, "_fetch_eod_akshare", side_effect=_fake_fetch), \
         patch.object(eod, "_insert_pg_rows", new=AsyncMock()) as mock_insert:
        r = await eod._incremental_sync_eod_akshare(
            codes=["sh600000", "sz000001"], start_str="20240101", end_str="20240120",
            old_calendar=old_dates, provider_uri=str(base),
            universe="all", days=1, overwrite=False,
        )
    assert r["ok"]
    mock_insert.assert_awaited_once()
    rows = mock_insert.await_args.args[0]
    assert len(rows) == 4  # 2 股 × 2 个新日期
    assert all(row["trade_date"] in ("2024-01-18", "2024-01-19") for row in rows)
    assert all(row["code"] in ("SH600000", "SZ000001") for row in rows)
    assert rows[0]["pct_chg"] == 2.0


async def test_baostock_no_candidate_dates_skips_akshare(tmp_qlib):
    """窗口内无新交易日（候选日期为空）→ 直接返回，不触发 akshare 全量爬。"""
    base, _ = tmp_qlib
    with patch.object(eod, "_gen_candidate_dates", return_value=[]), \
         patch.object(eod, "_incremental_sync_eod_akshare",
                      new=AsyncMock()) as mock_ak:
        r = await eod.incremental_sync_eod(
            universe="all", days=1, provider_uri=str(base),
            overwrite=False, source="baostock",
        )
    assert r["ok"] is True
    assert r["success"] == 0
    assert "无新交易日" in r.get("message", "")
    mock_ak.assert_not_awaited()


async def test_akshare_all_failed_returns_ok_false(tmp_qlib):
    """akshare 兜底全部拉取失败(success=0) → ok=False，避免被标记为成功。"""
    base, old_dates = tmp_qlib

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated fetch failure")

    with patch.object(eod, "_fetch_eod_akshare", side_effect=_boom):
        r = await eod._incremental_sync_eod_akshare(
            codes=["sh600000"], start_str="20240101", end_str="20240120",
            old_calendar=old_dates, provider_uri=str(base),
            universe="all", days=1, overwrite=False,
        )
    assert r["ok"] is False
    assert r["success"] == 0
    assert r["failed"] == 1
