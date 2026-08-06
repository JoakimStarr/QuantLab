# -*- coding: utf-8 -*-
"""etf_sync 单元测试。

覆盖：
- _etf_out_df：change/tradable/factor 派生（tradable=非停牌，无涨跌停判定）
- sync_etf_to_qlib：写 bin 对齐日历 + 返回 etf_daily pg_rows
- rebuild_etf_pool：全量池写入（不过滤）
- fetch_etf_history_tencent：腾讯 qfq 拉取（列序/量单位/成交额估算/涨跌幅自算）
- sync_etf_tencent_aligned：对齐现有时间范围回填
"""
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from app.services.data import etf_sync as es


# ---------- _etf_out_df ----------

def test_etf_out_df_derived_fields():
    df = pd.DataFrame({
        "date": ["2026-01-02"],
        "open": [4.0], "high": [4.1], "low": [3.9], "close": [4.05],
        "volume": [1000000.0], "amount": [4050000.0],
        "tradestatus": ["1"], "pctChg": [1.25],
    })
    out = es._etf_out_df(df)
    assert out["change"].iloc[0] == pytest.approx(0.0125)   # pctChg/100
    assert out["tradable"].iloc[0] == 1.0                    # 非停牌可交易
    assert out["factor"].iloc[0] == 1.0                      # 前复权存储
    assert out["close"].iloc[0] == pytest.approx(4.05)


def test_etf_out_df_suspended_not_tradable():
    df = pd.DataFrame({
        "date": ["2026-01-02"],
        "open": [2.0], "high": [2.1], "low": [1.9], "close": [2.05],
        "volume": [100.0], "amount": [205.0],
        "tradestatus": ["0"], "pctChg": [0.0],
    })
    out = es._etf_out_df(df)
    assert out["tradable"].iloc[0] == 0.0  # 停牌日不可交易


# ---------- sync_etf_to_qlib ----------

def _mock_etf_market(date):
    """模拟 query_daily_history_k_ETF 全市场返回（2 只 ETF）。"""
    return pd.DataFrame({
        "date": [date, date],
        "code": ["sh.510300", "sz.159915"],
        "open": [4.0, 2.0], "high": [4.1, 2.1], "low": [3.9, 1.9],
        "close": [4.05, 2.05], "preclose": [4.0, 2.0],
        "volume": [1000000.0, 500000.0], "amount": [4050000.0, 1025000.0],
        "adjustflag": ["3", "3"], "turn": [1.0, 2.0],
        "tradestatus": ["1", "0"], "pctChg": [1.25, 2.5],
        "peTTM": [None, None], "pbMRQ": [None, None],
        "psTTM": [None, None], "pcfNcfTTM": [None, None],
        "isST": [None, None],
    })


def test_sync_etf_writes_bins_and_pg_rows(tmp_path):
    """写 bin 对齐日历 + 返回 etf_daily 记录（含 tradable/change 正确）。"""
    base = tmp_path / "qlib"
    (base / "calendars").mkdir(parents=True)
    (base / "features").mkdir(parents=True)
    (base / "instruments").mkdir(parents=True)
    calendar = ["2026-01-02", "2026-01-05", "2026-01-06"]
    with open(base / "calendars" / "day.txt", "w") as f:
        f.write("\n".join(calendar) + "\n")
    new_date = "2026-01-07"

    with patch("app.services.data.baostock_client.fetch_etf_daily_sync",
               side_effect=lambda d: _mock_etf_market(d)):
        r = es.sync_etf_to_qlib(str(base), [new_date], calendar, overwrite=True)

    assert r["ok"]
    assert r["success"] == 2
    assert new_date in r["new_dates"]

    # bin 长度 = 4 头 + 4×日历长度
    import os
    close_bin = os.path.join(str(base), "features", "sh510300", "close.day.bin")
    size = os.path.getsize(close_bin)
    assert size == 4 + 4 * (len(calendar) + 1)

    # 复权对齐无旧 bin：close 原值写入
    from app.services.data.eod_incremental import _read_bin
    vals, start = _read_bin(os.path.join(str(base), "features", "sh510300", "close.day.bin"))
    assert start == 0
    assert vals[-1] == pytest.approx(4.05, abs=1e-4)

    # tradable：sh510300 非停牌=1，sz159915 停牌=0
    tv, _ = _read_bin(os.path.join(str(base), "features", "sh510300", "tradable.day.bin"))
    tv2, _ = _read_bin(os.path.join(str(base), "features", "sz159915", "tradable.day.bin"))
    assert tv[-1] == 1.0
    assert tv2[-1] == 0.0

    # etf_daily 记录
    assert len(r["pg_rows"]) == 2
    rows = {row["code"]: row for row in r["pg_rows"]}
    assert rows["SH510300"]["pct_chg"] == 1.25
    assert rows["SH510300"]["amount"] == pytest.approx(4050000.0)
    assert rows["SZ159915"]["trade_date"] == new_date


def test_sync_etf_skips_empty_and_non_trading(tmp_path):
    """非交易日返回空 → 跳过，success=0。"""
    base = tmp_path / "qlib"
    (base / "calendars").mkdir(parents=True)
    (base / "features").mkdir(parents=True)
    (base / "instruments").mkdir(parents=True)
    calendar = ["2026-01-02"]
    with open(base / "calendars" / "day.txt", "w") as f:
        f.write("\n".join(calendar) + "\n")

    with patch("app.services.data.baostock_client.fetch_etf_daily_sync",
               return_value=pd.DataFrame(columns=["date", "code"])):
        r = es.sync_etf_to_qlib(str(base), ["2026-01-03"], calendar)
    assert r["ok"]
    assert r["success"] == 0
    assert r["dates"] == []


# ---------- rebuild_etf_curated_pool ----------

class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self._i = 0

    async def execute(self, stmt):
        r = self._results[min(self._i, len(self._results) - 1)]
        self._i += 1
        return r

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


@pytest.mark.asyncio
async def test_rebuild_etf_pool_writes_all(tmp_path):
    """全量池：etf_daily 里有多少只就写多少只，不过滤、不筛选。"""
    base = tmp_path / "qlib"
    (base / "calendars").mkdir(parents=True)
    (base / "instruments").mkdir(parents=True)
    calendar = ["2026-01-01", "2026-01-02", "2026-01-05"]
    with open(base / "calendars" / "day.txt", "w") as f:
        f.write("\n".join(calendar) + "\n")

    # 3 只 ETF，历史长短各异——全量池不按历史/成交额过滤，全部写入
    fake = _FakeSession([_FakeResult([("SH510300",), ("SH510030",), ("SH510050",)])])

    with patch("app.core.database.async_session", return_value=fake):
        top = await es.rebuild_etf_pool(str(base))

    assert top == ["sh510030", "sh510050", "sh510300"]  # 按 code 升序，无筛选
    pool = (base / "instruments" / "etf_all.txt").read_text(encoding="utf-8").strip().split("\n")
    assert pool == ["sh510030\t2026-01-01\t2026-01-05",
                    "sh510050\t2026-01-01\t2026-01-05",
                    "sh510300\t2026-01-01\t2026-01-05"]


@pytest.mark.asyncio
async def test_rebuild_etf_pool_empty(tmp_path):
    """etf_daily 无数据时池文件为空列表（不写脏数据）。"""
    base = tmp_path / "qlib"
    (base / "calendars").mkdir(parents=True)
    (base / "instruments").mkdir(parents=True)
    with open(base / "calendars" / "day.txt", "w") as f:
        f.write("2026-01-01\n2026-01-02\n")

    fake = _FakeSession([_FakeResult([])])
    with patch("app.core.database.async_session", return_value=fake):
        top = await es.rebuild_etf_pool(str(base))
    assert top == []
    pool = (base / "instruments" / "etf_all.txt").read_text(encoding="utf-8").strip()
    assert pool == ""


# ---------- 腾讯 qfq 拉取 ----------

def test_fetch_etf_history_tencent():
    """腾讯 fqkline/get：列序转换/量手→股/成交额估算/涨跌幅自算。"""
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"data": {"sh510300": {"qfqday": [
        ["2026-01-05", "4.66", "4.721", "4.725", "4.657", "10171325.0"],
        ["2026-01-06", "4.70", "4.75", "4.76", "4.68", "12000000.0"],
    ]}}}
    with patch("requests.get", return_value=fake_resp):
        df = es.fetch_etf_history_tencent("sh510300", "2026-01-01", "2026-08-05")

    assert len(df) == 2
    assert df["date"].iloc[0] == "2026-01-05"
    assert df["open"].iloc[0] == 4.66 and df["close"].iloc[0] == 4.721
    assert df["high"].iloc[0] == 4.725 and df["low"].iloc[0] == 4.657
    assert df["volume"].iloc[0] == pytest.approx(1017132500.0)  # 手 × 100 → 股
    assert df["amount"].iloc[0] == pytest.approx(
        1017132500.0 * (4.66 + 4.725 + 4.657 + 4.721) / 4.0)
    assert df["tradestatus"].iloc[0] == 1
    assert df["pctChg"].iloc[0] == 0.0                          # 首日无涨跌幅
    assert df["pctChg"].iloc[1] == pytest.approx((4.75 / 4.721 - 1) * 100.0)


def test_fetch_etf_history_tencent_out_of_window_filtered():
    """窗口过滤：腾讯返回范围外的日期被剔除。"""
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"data": {"sh510300": {"qfqday": [
        ["2025-12-30", "4.5", "4.6", "4.7", "4.4", "100.0"],   # 早于窗口
        ["2026-01-05", "4.66", "4.721", "4.725", "4.657", "100.0"],
    ]}}}
    with patch("requests.get", return_value=fake_resp):
        df = es.fetch_etf_history_tencent("sh510300", "2026-01-01", "2026-08-05")
    assert len(df) == 1
    assert df["date"].iloc[0] == "2026-01-05"


# ---------- 腾讯对齐回填 ----------

@pytest.mark.asyncio
async def test_sync_etf_tencent_aligned(tmp_path):
    """对齐现有时间范围：写 bin + upsert etf_daily，不扩展日历。"""
    base = tmp_path / "qlib"
    (base / "calendars").mkdir(parents=True)
    (base / "features").mkdir(parents=True)
    (base / "instruments").mkdir(parents=True)
    calendar = ["2026-01-05", "2026-01-06", "2026-01-07"]
    with open(base / "calendars" / "day.txt", "w") as f:
        f.write("\n".join(calendar) + "\n")

    fake_df = pd.DataFrame({
        "date": ["2026-01-05", "2026-01-06"],
        "open": [4.66, 4.70], "high": [4.725, 4.76], "low": [4.657, 4.68],
        "close": [4.721, 4.75], "volume": [1e9, 1.1e9],
        "amount": [4.7e9, 5e9], "pctChg": [0.0, 0.61], "tradestatus": [1, 1],
    })

    with patch.object(es, "_load_etf_min_date", new=AsyncMock(return_value="2026-01-05")), \
         patch.object(es, "_load_etf_codes_from_db", new=AsyncMock(return_value=["SH510300"])), \
         patch.object(es, "fetch_etf_history_tencent", return_value=fake_df), \
         patch.object(es, "_insert_etf_daily", new=AsyncMock()) as m_insert, \
         patch.object(es, "rebuild_etf_pool", new=AsyncMock(return_value=["sh510300"])), \
         patch.object(es, "_register_synced_etfs", new=AsyncMock(return_value=1)), \
         patch.object(es, "_save_tencent_done"):
        r = await es.sync_etf_tencent_aligned(str(base))

    assert r["ok"]
    assert r["source"] == "tencent"
    assert r["success"] == 1
    assert r["window"] == ["2026-01-05", "2026-01-07"]
    m_insert.assert_awaited()  # upsert 落库

    # bin 对齐主日历：长度 = 日历长度，未对齐日期为 NaN
    from app.services.data.eod_incremental import _read_bin
    close, start = _read_bin(str(base / "features" / "sh510300" / "close.day.bin"))
    assert start == 0
    assert len(close) == len(calendar)
    assert close[0] == pytest.approx(4.721, abs=1e-4)
    assert np.isnan(close[2])  # 01-07 无数据


@pytest.mark.asyncio
async def test_sync_etf_tencent_aligned_no_existing_data(tmp_path):
    """etf_daily 无数据时回退 days 窗口。"""
    base = tmp_path / "qlib"
    (base / "calendars").mkdir(parents=True)
    (base / "features").mkdir(parents=True)
    (base / "instruments").mkdir(parents=True)
    calendar = ["2026-01-05", "2026-01-06"]
    with open(base / "calendars" / "day.txt", "w") as f:
        f.write("\n".join(calendar) + "\n")

    fake_df = pd.DataFrame({
        "date": ["2026-01-05"],
        "open": [4.66], "high": [4.725], "low": [4.657],
        "close": [4.721], "volume": [1e9], "amount": [4.7e9],
        "pctChg": [0.0], "tradestatus": [1],
    })
    with patch.object(es, "_load_etf_min_date", new=AsyncMock(return_value=None)), \
         patch.object(es, "_load_etf_codes_from_db", new=AsyncMock(return_value=["SH510300"])), \
         patch.object(es, "fetch_etf_history_tencent", return_value=fake_df), \
         patch.object(es, "_insert_etf_daily", new=AsyncMock()), \
         patch.object(es, "rebuild_etf_pool", new=AsyncMock(return_value=["sh510300"])), \
         patch.object(es, "_register_synced_etfs", new=AsyncMock(return_value=1)), \
         patch.object(es, "_save_tencent_done"):
        r = await es.sync_etf_tencent_aligned(str(base), days=30)
    assert r["ok"]
    assert r["window"][1] == "2026-01-06"  # 对齐主日历末日


@pytest.mark.asyncio
async def test_sync_etf_tencent_skips_done(tmp_path):
    """断点续跑：overwrite=False 时跳过已完成清单中的代码，不再请求。"""
    base = tmp_path / "qlib"
    (base / "calendars").mkdir(parents=True)
    (base / "features").mkdir(parents=True)
    (base / "instruments").mkdir(parents=True)
    with open(base / "calendars" / "day.txt", "w") as f:
        f.write("2026-01-05\n2026-01-06\n")

    with patch.object(es, "_load_etf_min_date", new=AsyncMock(return_value="2026-01-05")), \
         patch.object(es, "_load_etf_codes_from_db", new=AsyncMock(return_value=["SH510300", "SH510050"])), \
         patch.object(es, "_load_tencent_done", return_value={"SH510300"}), \
         patch.object(es, "fetch_etf_history_tencent", return_value=None) as m_fetch, \
         patch.object(es, "_insert_etf_daily", new=AsyncMock()), \
         patch.object(es, "rebuild_etf_pool", new=AsyncMock(return_value=["sh510300"])), \
         patch.object(es, "_register_synced_etfs", new=AsyncMock(return_value=1)), \
         patch.object(es, "_save_tencent_done"):
        r = await es.sync_etf_tencent_aligned(str(base), overwrite=False)

    # SH510300 已完成被跳过，只请求 SH510050（返回 None → 失败 1）
    assert r["ok"]
    assert r["success"] == 0
    assert r["failed"] == 1
    assert m_fetch.call_count == 1
