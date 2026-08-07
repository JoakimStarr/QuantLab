# -*- coding: utf-8 -*-
"""macro_sync 单元测试。

覆盖：
- 东财响应解析（jQuery 包裹 JSON / 纯 JSON / 非法）
- _build_macro_rows 的 available_date PIT 偏移
- forward_fill_to_daily 的日历对齐与前向填充
- broadcast_to_all_stocks 的 bin 写入尺寸
- 窄表唯一键冲突幂等（mock DB 层或跳过）
"""
import os

import numpy as np
import pandas as pd
import pytest

from app.services.data import macro_sync as ms
from app.services.data.eod_incremental import _read_bin


# ---------- 东财响应解析 ----------

def test_parse_jquery_wrapped_json():
    text = 'jQuery1123_x({"message":"ok","result":{"data":[{"A":1}]}});'
    parsed = ms._parse_eastmoney_response(text)
    assert parsed["message"] == "ok"
    assert parsed["result"]["data"][0]["A"] == 1


def test_parse_pure_json():
    parsed = ms._parse_eastmoney_response('{"message":"ok","result":null}')
    assert parsed["message"] == "ok"


def test_parse_invalid():
    assert ms._parse_eastmoney_response("") is None
    assert ms._parse_eastmoney_response("not json at all") is None
    assert ms._parse_eastmoney_response("jQuery( broken") is None


# ---------- available_date PIT 偏移 ----------

def test_build_macro_rows_pmi_delay_zero():
    df = pd.DataFrame({
        "REPORT_DATE": pd.to_datetime(["2026-07-01"]),
        "MAKE_INDEX": [49.2],
        "NMAKE_INDEX": [50.1],
    })
    rows = ms._build_macro_rows(df, "PMI")
    assert len(rows) == 2
    by_field = {r["field_name"]: r for r in rows}
    # PMI delay=0 → available_date == report_date
    assert by_field["pmi"]["value"] == 49.2
    assert by_field["pmi"]["available_date"].isoformat() == "2026-07-01"
    assert by_field["pmi_nm"]["value"] == 50.1


def test_build_macro_rows_cpi_delay_nine():
    df = pd.DataFrame({
        "REPORT_DATE": pd.to_datetime(["2026-06-01"]),
        "NATIONAL_SAME": [1.0],
    })
    rows = ms._build_macro_rows(df, "CPI")
    assert len(rows) == 1
    # CPI delay=9 → available_date = report_date + 9 天
    assert rows[0]["available_date"].isoformat() == "2026-06-10"
    assert rows[0]["field_name"] == "cpi"


def test_build_macro_rows_skips_nan():
    df = pd.DataFrame({
        "REPORT_DATE": pd.to_datetime(["2026-06-01"]),
        "NATIONAL_SAME": [np.nan],
    })
    rows = ms._build_macro_rows(df, "CPI")
    assert len(rows) == 0


# ---------- forward-fill 到日历 ----------

@pytest.fixture
def tmp_qlib(tmp_path):
    """临时 qlib 目录：日历 5 天。"""
    base = tmp_path / "qlib"
    (base / "calendars").mkdir(parents=True)
    (base / "features" / "sh600000").mkdir(parents=True)
    dates = ["2024-01-15", "2024-01-16", "2024-01-17", "2024-01-18", "2024-01-19"]
    with open(base / "calendars" / "day.txt", "w") as f:
        f.write("\n".join(dates) + "\n")
    return base


def test_forward_fill_aligns_to_calendar(tmp_qlib):
    """月度值在 available_date 起生效并 forward-fill。"""
    # 两个月度值：1-16 可用、1-18 可用
    series = pd.Series(
        [50.0, 51.0],
        index=pd.to_datetime(["2024-01-16", "2024-01-18"]),
    )
    values = ms.forward_fill_to_daily(str(tmp_qlib), "pmi", series)
    assert len(values) == 5
    # 1-15 NaN，1-16/17=50，1-18/19=51
    assert np.isnan(values[0])
    assert values[1] == 50.0
    assert values[2] == 50.0
    assert values[3] == 51.0
    assert values[4] == 51.0


def test_forward_fill_empty_series(tmp_qlib):
    series = pd.Series(dtype=float)
    values = ms.forward_fill_to_daily(str(tmp_qlib), "pmi", series)
    assert len(values) == 5
    assert np.isnan(values).all()


# ---------- 广播写 bin ----------

def test_broadcast_writes_bin_with_correct_size(tmp_qlib):
    values = np.array([np.nan, 50.0, 50.0, 51.0, 51.0], dtype=np.float32)
    n = ms.broadcast_to_all_stocks(str(tmp_qlib), "pmi", values)
    assert n == 1  # 只有 sh600000 一个目录

    bin_path = os.path.join(str(tmp_qlib), "features", "sh600000", "pmi.day.bin")
    data, start = _read_bin(bin_path)
    assert start == 0
    assert len(data) == 5  # 4字节头 + 5*4 = 24 字节
    assert np.isnan(data[0])
    assert data[1] == 50.0
    assert data[4] == 51.0


def test_broadcast_empty_values_no_write(tmp_qlib):
    n = ms.broadcast_to_all_stocks(str(tmp_qlib), "pmi", np.array([], dtype=np.float32))
    assert n == 0
    assert not os.path.exists(os.path.join(str(tmp_qlib), "features", "sh600000", "pmi.day.bin"))


# ---------- 并行模式（progress_cb） ----------

@pytest.mark.asyncio
async def test_sync_macro_indicators_progress_cb_does_not_touch_global_progress(tmp_qlib):
    """并行模式（progress_cb 传入）不 init/finish/clear 全局进度，只走回调。

    一键全同步并行执行宏观/财报/外盘时，若各阶段各自操作共享进度文件，
    会互相覆盖造成竞态——必须统一由 full_sync 管理。
    """
    from unittest.mock import AsyncMock, patch

    reports = []

    def _cb(pct, msg):
        reports.append((pct, msg))

    with patch("app.services.data.sync_progress.init_progress") as mock_init, \
         patch("app.services.data.sync_progress.update_progress") as mock_update, \
         patch("app.services.data.sync_progress.finish_progress") as mock_finish, \
         patch("app.services.data.sync_progress.clear_progress") as mock_clear, \
         patch.object(ms, "_fetch_all_macro_rows", new=AsyncMock(return_value=([], {}))), \
         patch.object(ms, "upsert_macro", new=AsyncMock(return_value=0)), \
         patch.object(ms, "broadcast_macro_to_bins", new=AsyncMock(return_value=0)):
        result = await ms.sync_macro_indicators(broadcast=True, progress_cb=_cb)

    assert result["ok"] is True
    assert reports  # 走回调上报了进度
    mock_init.assert_not_called()
    mock_update.assert_not_called()
    mock_finish.assert_not_called()
    mock_clear.assert_not_called()
