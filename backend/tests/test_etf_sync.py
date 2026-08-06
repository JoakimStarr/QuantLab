# -*- coding: utf-8 -*-
"""etf_sync 单元测试。

覆盖：
- _etf_out_df：change/tradable/factor 派生（tradable=非停牌，无涨跌停判定）
- sync_etf_to_qlib：写 bin 对齐日历 + 返回 etf_daily pg_rows
- rebuild_etf_curated_pool：按近 N 日均额筛选 + 最短历史过滤
"""
from unittest.mock import AsyncMock, patch

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
