# -*- coding: utf-8 -*-
"""global_macro_sync 单元测试。

覆盖：
- _iso_date 日期解析（ISO / 带时间后缀）
- FRED 拉取归一化（含缺 key 降级、缺失值跳过）
- CFTC 非商业净多计算
- EIA 周度库存归一化
- 因子表达式白名单包含新 $ 字段
- 广播复用 forward_fill_to_daily/broadcast_to_all_stocks（bin 尺寸对齐）
"""
import os

import numpy as np
import pandas as pd
import pytest

from app.services.data import global_macro_sync as gm
from app.services.data.eod_incremental import _read_bin
from app.services.factor.expression import _QLIB_FIELDS


# ---------- 日期解析 ----------

def test_iso_date_plain():
    assert gm._iso_date("2024-01-15") == pd.Timestamp("2024-01-15").date()


def test_iso_date_with_time_suffix():
    assert gm._iso_date("2024-01-15T00:00:00.000") == pd.Timestamp("2024-01-15").date()


def test_iso_date_invalid():
    assert gm._iso_date(None) is None
    assert gm._iso_date("") is None
    assert gm._iso_date("not-a-date") is None


# ---------- FRED ----------

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_fetch_fred_missing_key_returns_empty(monkeypatch):
    monkeypatch.setattr(gm.settings, "fred_api_key", "")
    cfg = {"type": "fred", "delay": 0, "fields": {"us_fed_rate": {"series_id": "DFF", "unit": "%"}}}
    assert gm._fetch_fred("FRED_RATES", cfg) == []


def test_fetch_fred_normalizes_rows(monkeypatch):
    monkeypatch.setattr(gm.settings, "fred_api_key", "testkey")
    payload = {"observations": [
        {"date": "2024-01-01", "value": "5.33"},
        {"date": "2024-01-02", "value": "."},  # 缺失值跳过
        {"date": "2024-01-03", "value": "5.25"},
    ]}

    def fake_get(url, params=None, timeout=None):
        return _FakeResp(payload)

    monkeypatch.setattr("requests.get", fake_get)
    cfg = {"type": "fred", "delay": 0, "fields": {"us_fed_rate": {"series_id": "DFF", "unit": "%"}}}
    rows = gm._fetch_fred("FRED_RATES", cfg)
    assert len(rows) == 2
    assert rows[0]["indicator"] == "FRED_RATES"
    assert rows[0]["field_name"] == "us_fed_rate"
    assert rows[0]["value"] == 5.33
    assert rows[0]["source"] == "fred"
    # delay=0 → available_date == report_date
    assert rows[0]["available_date"].isoformat() == "2024-01-01"


def test_fetch_fred_respects_units_param(monkeypatch):
    monkeypatch.setattr(gm.settings, "fred_api_key", "testkey")
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured.update(params or {})
        return _FakeResp({"observations": []})

    monkeypatch.setattr("requests.get", fake_get)
    cfg = {"type": "fred", "delay": 30,
           "fields": {"us_cpi_yoy": {"series_id": "CPIAUCSL", "units": "pc1", "unit": "%"}}}
    gm._fetch_fred("FRED_INFLATION", cfg)
    assert captured["series_id"] == "CPIAUCSL"
    assert captured["units"] == "pc1"
    assert captured["api_key"] == "testkey"


# ---------- CFTC ----------

def test_fetch_cftc_computes_net_position(monkeypatch):
    data = [
        {"report_date_as_yyyy_mm_dd": "2024-01-09T00:00:00.000",
         "noncomm_positions_long_all": "200", "noncomm_positions_short_all": "50"},
        {"report_date_as_yyyy_mm_dd": "2024-01-02T00:00:00.000",
         "noncomm_positions_long_all": "180", "noncomm_positions_short_all": "90"},
    ]
    monkeypatch.setattr("requests.get", lambda url, params=None, timeout=None: _FakeResp(data))
    cfg = {"type": "cftc", "delay": 3,
           "fields": {"gold_cot_net": {"market": "GOLD - COMMODITY EXCHANGE INC.", "unit": "手"}}}
    rows = gm._fetch_cftc("CFTC_COT", cfg)
    assert len(rows) == 2
    assert rows[0]["value"] == 150.0  # 200 - 50
    assert rows[1]["value"] == 90.0   # 180 - 90
    assert rows[0]["source"] == "cftc"
    # delay=3 → available_date = report_date + 3
    assert rows[0]["available_date"].isoformat() == "2024-01-12"


# ---------- EIA ----------

def test_fetch_eia_missing_key_returns_empty(monkeypatch):
    monkeypatch.setattr(gm.settings, "eia_api_key", "")
    cfg = {"type": "eia", "delay": 7,
           "fields": {"us_crude_stock": {"series_id": "WGTSTUS1", "unit": "千桶"}}}
    assert gm._fetch_eia("EIA_CRUDE", cfg) == []


def test_fetch_eia_normalizes_rows(monkeypatch):
    monkeypatch.setattr(gm.settings, "eia_api_key", "testkey")
    payload = {"response": {"data": [
        {"period": "2024-01-05", "value": "431000"},
        {"period": "2023-12-29", "value": "432000"},
    ]}}
    monkeypatch.setattr("requests.get", lambda url, params=None, timeout=None: _FakeResp(payload))
    cfg = {"type": "eia", "delay": 7,
           "fields": {"us_crude_stock": {"series_id": "WGTSTUS1", "unit": "千桶"}}}
    rows = gm._fetch_eia("EIA_CRUDE", cfg)
    assert len(rows) == 2
    assert rows[0]["value"] == 431000.0
    assert rows[0]["source"] == "eia"
    assert rows[0]["available_date"].isoformat() == "2024-01-12"


# ---------- 白名单 ----------

def test_expression_whitelist_includes_global_fields():
    for f in [
        "$us_fed_rate", "$ecb_rate", "$us_cpi_yoy", "$us_unrate",
        "$us_ism_pmi", "$us_nonfarm", "$gold_cot_net", "$copper_cot_net",
        "$crude_cot_net", "$us_crude_stock",
        "$us_trsy2y", "$us_trsy10y", "$us_trsy_spread",
    ]:
        assert f in _QLIB_FIELDS, f"白名单缺失 {f}"


# ---------- 广播复用 ----------

@pytest.fixture
def tmp_qlib(tmp_path):
    """临时 qlib 目录：日历 5 天 + 一只股票。"""
    base = tmp_path / "qlib"
    (base / "calendars").mkdir(parents=True)
    (base / "features" / "sh600000").mkdir(parents=True)
    dates = ["2024-01-15", "2024-01-16", "2024-01-17", "2024-01-18", "2024-01-19"]
    with open(base / "calendars" / "day.txt", "w") as f:
        f.write("\n".join(dates) + "\n")
    return base


def test_broadcast_global_macro_writes_bin(tmp_qlib):
    values = np.array([np.nan, 5.0, 5.0, 5.25, 5.25], dtype=np.float32)
    n = gm.broadcast_to_all_stocks(str(tmp_qlib), "us_fed_rate", values)
    assert n == 1
    data, start = _read_bin(os.path.join(str(tmp_qlib), "features", "sh600000", "us_fed_rate.day.bin"))
    assert start == 0
    assert len(data) == 5
    assert np.isnan(data[0])
    assert data[4] == 5.25
