# -*- coding: utf-8 -*-
"""index_sync (baostock 版) 单元测试。

覆盖：
- _get_index_list 优先级（参数 > config > 默认）
- sync_indices_to_qlib 写 bin 逻辑（mock baostock 拉取，验证 bin 写入与日历对齐）
- 日历不存在/为空时的错误处理
"""
from pathlib import Path
from unittest.mock import patch, MagicMock
import struct

import numpy as np
import pandas as pd
import pytest

from app.services.data import index_sync


# ---------- _get_index_list ----------

def test_get_index_list_param_priority():
    """参数 > config > 默认"""
    assert index_sync._get_index_list(["sh000999"]) == ["sh000999"]


def test_get_index_list_from_config():
    """config.quant.sync_indices 配置优先于默认"""
    mock_settings = MagicMock()
    mock_settings.quant = {"sync_indices": ["sh000300", "sh000016"]}
    with patch('app.services.data.index_sync.settings', mock_settings):
        result = index_sync._get_index_list(None)
    assert result == ["sh000300", "sh000016"]


def test_get_index_list_default():
    """无参数无 config 时用默认 8 大指数"""
    mock_settings = MagicMock()
    mock_settings.quant = {}
    with patch('app.services.data.index_sync.settings', mock_settings):
        result = index_sync._get_index_list(None)
    assert "sh000001" in result
    assert "sh000300" in result
    assert len(result) == 8


# ---------- sync_indices_to_qlib ----------

def _make_calendar(tmp_path: Path, dates: list):
    """构造 day.txt + features 目录"""
    cal_dir = tmp_path / "calendars"
    cal_dir.mkdir(parents=True, exist_ok=True)
    with open(cal_dir / "day.txt", "w") as f:
        for d in dates:
            f.write(d + "\n")
    (tmp_path / "features").mkdir(exist_ok=True)
    return str(tmp_path)


def _read_bin(bin_path: str):
    """读取 qlib bin 文件，返回 (values, start_index)"""
    with open(bin_path, "rb") as f:
        hdr = f.read(4)
        start = int(round(struct.unpack("<f", hdr)[0]))
        data = np.fromfile(f, dtype="<f4")
    return data, start


def test_sync_indices_writes_bin(tmp_path):
    """mock baostock 返回指数数据，验证 bin 写入与日历对齐"""
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    provider_uri = _make_calendar(tmp_path, dates)

    # mock _fetch_index_via_baostock 返回 3 天指数数据
    mock_df = pd.DataFrame({
        "date": dates,
        "open": [3000.0, 3050.0, 3080.0],
        "high": [3020.0, 3060.0, 3100.0],
        "low": [2990.0, 3040.0, 3070.0],
        "close": [3010.0, 3055.0, 3090.0],
        "volume": [1e8, 1.2e8, 1.5e8],
        "amount": [3e10, 3.6e10, 4.5e10],
    })
    with patch('app.services.data.index_sync._fetch_index_via_baostock',
               return_value=mock_df):
        result = index_sync.sync_indices_to_qlib(
            provider_uri, indices=["sh000001"]
        )

    assert result["ok"] is True
    assert result["success"] == 1
    assert result["failed"] == 0
    assert result["source"] == "baostock"
    assert "sh000001" in result["indices"]

    # 验证 bin 文件写入正确
    close_bin = tmp_path / "features" / "sh000001" / "close.day.bin"
    assert close_bin.exists()
    values, start = _read_bin(str(close_bin))
    assert start == 0
    assert len(values) == 3
    assert values[0] == pytest.approx(3010.0, rel=1e-5)
    assert values[2] == pytest.approx(3090.0, rel=1e-5)


def test_sync_indices_no_calendar(tmp_path):
    """日历不存在 → 返回错误"""
    result = index_sync.sync_indices_to_qlib(str(tmp_path), indices=["sh000001"])
    assert result["ok"] is False
    assert "日历文件不存在" in result["error"]


def test_sync_indices_empty_calendar(tmp_path):
    """日历为空 → 返回错误"""
    cal_dir = tmp_path / "calendars"
    cal_dir.mkdir(parents=True)
    (cal_dir / "day.txt").write_text("")
    result = index_sync.sync_indices_to_qlib(str(tmp_path), indices=["sh000001"])
    assert result["ok"] is False
    assert "日历为空" in result["error"]


def test_sync_indices_filtered_by_calendar(tmp_path):
    """baostock 返回的日期不在日历中时被过滤"""
    dates = ["2024-01-02", "2024-01-03"]
    provider_uri = _make_calendar(tmp_path, dates)

    # mock 返回包含日历外日期的数据
    mock_df = pd.DataFrame({
        "date": ["2024-01-02", "2024-01-03", "2024-01-05"],  # 01-05 不在日历
        "open": [3000.0, 3050.0, 3100.0],
        "high": [3020.0, 3060.0, 3120.0],
        "low": [2990.0, 3040.0, 3090.0],
        "close": [3010.0, 3055.0, 3110.0],
        "volume": [1e8, 1.2e8, 1.5e8],
        "amount": [3e10, 3.6e10, 4.5e10],
    })
    with patch('app.services.data.index_sync._fetch_index_via_baostock',
               return_value=mock_df):
        result = index_sync.sync_indices_to_qlib(
            provider_uri, indices=["sh000300"]
        )

    assert result["ok"] is True
    # 验证 bin 长度 == 日历长度（2），不是 mock 数据长度（3）
    close_bin = tmp_path / "features" / "sh000300" / "close.day.bin"
    values, _ = _read_bin(str(close_bin))
    assert len(values) == 2


def test_sync_indices_handles_fetch_failure(tmp_path):
    """单个指数拉取失败时计入 failed，不影响其他指数"""
    dates = ["2024-01-02"]
    provider_uri = _make_calendar(tmp_path, dates)

    mock_df = pd.DataFrame({
        "date": ["2024-01-02"],
        "open": [3000.0], "high": [3020.0], "low": [2990.0],
        "close": [3010.0], "volume": [1e8], "amount": [3e10],
    })

    def side_effect(code, start, end):
        if code == "sh000999":
            raise RuntimeError("fetch failed")
        return mock_df

    with patch('app.services.data.index_sync._fetch_index_via_baostock',
               side_effect=side_effect):
        result = index_sync.sync_indices_to_qlib(
            provider_uri, indices=["sh000001", "sh000999"]
        )

    assert result["ok"] is True
    assert result["success"] == 1
    assert result["failed"] == 1
