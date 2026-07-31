# -*- coding: utf-8 -*-
"""smart_sync 智能同步路径判断单元测试。

覆盖 predict_sync_path 的核心路径判断逻辑：
  - qlib 数据不存在 → chenditc_full
  - latest_date 距今 > 阈值 → chenditc_full
  - latest_date 距今 1-阈值 天 → baostock_incremental
  - latest_date 是今天(距今 0 天) → baostock_today
"""
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from app.services.data import smart_sync


def _make_calendar(tmp_path: Path, latest_date_str: str):
    """构造 day.txt，末行为 latest_date_str。"""
    cal_dir = tmp_path / "calendars"
    cal_dir.mkdir(parents=True, exist_ok=True)
    with open(cal_dir / "day.txt", "w") as f:
        f.write("2020-01-01\n")
        f.write(latest_date_str + "\n")
    return str(tmp_path)


def test_predict_path_no_data(tmp_path):
    """qlib_dir 不存在 → chenditc_full"""
    result = smart_sync.predict_sync_path(str(tmp_path / "not_exist"))
    assert result["path"] == "chenditc_full"
    assert result["latest_date"] is None
    assert result["days_behind"] is None


def test_predict_path_empty_calendar(tmp_path):
    """day.txt 为空 → chenditc_full"""
    cal_dir = tmp_path / "calendars"
    cal_dir.mkdir(parents=True)
    (cal_dir / "day.txt").write_text("")
    result = smart_sync.predict_sync_path(str(tmp_path))
    assert result["path"] == "chenditc_full"


def test_predict_path_stale(tmp_path):
    """latest_date 距今 8 天 → chenditc_full (>7)"""
    latest = (datetime.now().date() - timedelta(days=8)).strftime("%Y-%m-%d")
    provider_uri = _make_calendar(tmp_path, latest)
    result = smart_sync.predict_sync_path(provider_uri)
    assert result["path"] == "chenditc_full"
    assert result["days_behind"] == 8
    assert result["latest_date"] == latest


def test_predict_path_threshold_boundary(tmp_path):
    """latest_date 距今 7 天 → baostock_incremental (7 == 阈值，不大于)"""
    latest = (datetime.now().date() - timedelta(days=7)).strftime("%Y-%m-%d")
    provider_uri = _make_calendar(tmp_path, latest)
    result = smart_sync.predict_sync_path(provider_uri)
    assert result["path"] == "baostock_incremental"
    assert result["days_behind"] == 7


def test_predict_path_incremental(tmp_path):
    """latest_date 距今 3 天 → baostock_incremental"""
    latest = (datetime.now().date() - timedelta(days=3)).strftime("%Y-%m-%d")
    provider_uri = _make_calendar(tmp_path, latest)
    result = smart_sync.predict_sync_path(provider_uri)
    assert result["path"] == "baostock_incremental"
    assert result["days_behind"] == 3


def test_predict_path_yesterday(tmp_path):
    """latest_date 是昨天(距今1天) → baostock_incremental"""
    latest = (datetime.now().date() - timedelta(days=1)).strftime("%Y-%m-%d")
    provider_uri = _make_calendar(tmp_path, latest)
    result = smart_sync.predict_sync_path(provider_uri)
    assert result["path"] == "baostock_incremental"
    assert result["days_behind"] == 1


def test_predict_path_today(tmp_path):
    """latest_date 是今天(距今0天) → baostock_today"""
    latest = datetime.now().date().strftime("%Y-%m-%d")
    provider_uri = _make_calendar(tmp_path, latest)
    result = smart_sync.predict_sync_path(provider_uri)
    assert result["path"] == "baostock_today"
    assert result["days_behind"] == 0


def test_predict_path_custom_threshold(tmp_path):
    """自定义阈值：距今3天 > 阈值2 → chenditc_full"""
    latest = (datetime.now().date() - timedelta(days=3)).strftime("%Y-%m-%d")
    provider_uri = _make_calendar(tmp_path, latest)
    mock_settings = MagicMock()
    mock_settings.qlib_provider_path = provider_uri
    mock_settings.quant = {"smart_sync": {"full_sync_threshold_days": 2}}
    with patch('app.services.data.smart_sync.settings', mock_settings):
        result = smart_sync.predict_sync_path()
    assert result["path"] == "chenditc_full"


def test_predict_path_default_uses_provider_uri(tmp_path):
    """未传 provider_uri 时使用 settings.qlib_provider_path"""
    latest = datetime.now().date().strftime("%Y-%m-%d")
    _make_calendar(tmp_path, latest)
    mock_settings = MagicMock()
    mock_settings.qlib_provider_path = str(tmp_path)
    mock_settings.quant = {}
    with patch('app.services.data.smart_sync.settings', mock_settings):
        result = smart_sync.predict_sync_path()
    assert result["path"] == "baostock_today"


async def test_smart_sync_dispatch_chenditc_when_no_data(tmp_path):
    """smart_sync 在 qlib 数据不存在时调用 _sync_via_chenditc"""
    provider_uri = str(tmp_path / "not_exist")
    mock_settings = MagicMock()
    mock_settings.qlib_provider_path = provider_uri
    mock_settings.quant = {"smart_sync": {"full_sync_threshold_days": 7,
                                          "include_intraday": True},
                           "universe": "csi300"}
    with patch('app.services.data.smart_sync.settings', mock_settings), \
         patch('app.services.data.smart_sync.async_session') as mock_session, \
         patch('app.services.data.sync_runner._sync_via_chenditc',
               new=AsyncMock(return_value={
                   "latest_date": "2024-01-01", "stock_count": 100,
                   "row_count": 1000, "qlib_dir": provider_uri,
               })) as mock_chenditc, \
         patch('app.services.data.sync_runner.collect_qlib_stats',
               return_value={"latest_date": "2024-01-01",
                             "stock_count": 100, "row_count": 1000}):
        # mock async_session context manager
        session_instance = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session_instance.execute = AsyncMock(return_value=result_mock)
        mock_session.return_value.__aenter__ = AsyncMock(return_value=session_instance)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await smart_sync.smart_sync(universe="csi300")
        assert result["path"] == "chenditc_full"
        assert mock_chenditc.called
