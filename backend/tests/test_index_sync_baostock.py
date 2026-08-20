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


# ---------- 连续失败熔断 / akshare 兜底（2026-08 优化） ----------

def _mock_index_df(dates):
    """单指数日K（akshare 兜底返回值）。"""
    return pd.DataFrame({
        "date": dates,
        "open": [3000.0] * len(dates), "high": [3020.0] * len(dates),
        "low": [2990.0] * len(dates), "close": [3010.0] * len(dates),
        "volume": [1e8] * len(dates),
    })


def test_sync_indices_baostock_error_falls_back_to_akshare(tmp_path):
    """baostock 抛异常时也走 akshare 兜底（回归：此前直接标记失败）。"""
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    provider_uri = _make_calendar(tmp_path, dates)
    with patch('app.services.data.index_sync._fetch_index_via_baostock',
               side_effect=RuntimeError("10002007 网络接收错误")), \
         patch('app.services.data.index_sync._fetch_index_via_akshare',
               return_value=_mock_index_df(dates)) as m_ak:
        result = index_sync.sync_indices_to_qlib(provider_uri, indices=["sh000001"])
    assert result["ok"] is True
    assert result["success"] == 1
    assert result["failed"] == 0
    assert result["source"] == "akshare"
    assert m_ak.call_count == 1


def test_sync_indices_circuit_breaker_switches_to_akshare(tmp_path):
    """连续 5 失败重建会话 + 再 3 失败 → 熔断 baostock，剩余指数直接走 akshare。"""
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    provider_uri = _make_calendar(tmp_path, dates)
    indices = [f"sh{i:06d}" for i in range(10)]
    with patch('app.services.data.index_sync._fetch_index_via_baostock',
               side_effect=RuntimeError("10002007 网络接收错误")) as m_bs, \
         patch('app.services.data.index_sync._fetch_index_via_akshare',
               return_value=_mock_index_df(dates)) as m_ak, \
         patch('app.services.data.baostock_client.ensure_logout') as m_logout, \
         patch('app.services.data.baostock_client._ensure_login'):
        result = index_sync.sync_indices_to_qlib(provider_uri, indices=indices)
    # 5 次失败触发重建 + 重建后 3 次失败熔断 = baostock 共请求 8 次
    assert m_bs.call_count == 8
    assert m_logout.call_count == 1
    # 熔断后剩余 2 只跳过 baostock 直接 akshare；前 8 只兜底也走 akshare
    assert m_ak.call_count == 10
    assert result["success"] == 10
    assert result["failed"] == 0
    assert result["source"] == "akshare"
    # baostock 熔断但 akshare 兜底成功：aborted=False，abort_reason 仍记录原因
    assert result["aborted"] is False
    assert "熔断" in (result["abort_reason"] or "")


def test_sync_indices_quota_error_aborts(tmp_path):
    """配额耗尽中止整个指数同步，akshare 兜底也不执行。"""
    from app.services.data.baostock_client import BaostockQuotaError
    dates = ["2024-01-02", "2024-01-03"]
    provider_uri = _make_calendar(tmp_path, dates)
    with patch('app.services.data.index_sync._fetch_index_via_baostock',
               side_effect=BaostockQuotaError("当日配额已耗尽")), \
         patch('app.services.data.index_sync._fetch_index_via_akshare',
               return_value=_mock_index_df(dates)) as m_ak:
        result = index_sync.sync_indices_to_qlib(
            provider_uri, indices=["sh000001", "sh000300"])
    assert m_ak.call_count == 0  # 配额耗尽直接中止，不逐只兜底
    assert result["aborted"] is True
    assert result["success"] == 0
    assert result["total"] == 2


def test_sync_indices_circuit_breaker_when_relogin_fails(tmp_path):
    """会话重建失败也必须熔断切换 akshare（回归：relogin_done 未标记，
    baostock 全挂时每只指数都先空试 baostock 再兜底，纯浪费）。"""
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    provider_uri = _make_calendar(tmp_path, dates)
    indices = [f"sh{i:06d}" for i in range(10)]
    with patch('app.services.data.index_sync._fetch_index_via_baostock',
               side_effect=RuntimeError("10002007 网络接收错误")) as m_bs, \
         patch('app.services.data.index_sync._fetch_index_via_akshare',
               return_value=_mock_index_df(dates)) as m_ak, \
         patch('app.services.data.baostock_client.ensure_logout'), \
         patch('app.services.data.baostock_client._ensure_login',
               side_effect=RuntimeError("login failed")):
        result = index_sync.sync_indices_to_qlib(provider_uri, indices=indices)
    # 5 次失败 → 重建失败（consecutive_fail 保持 5）→ 第 6 次失败即熔断
    assert m_bs.call_count == 6
    assert m_ak.call_count == 10  # 全部走 akshare 兜底成功
    assert result["success"] == 10
    assert result["source"] == "akshare"
    assert "熔断" in (result["abort_reason"] or "")
