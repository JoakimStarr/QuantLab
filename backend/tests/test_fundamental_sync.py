# -*- coding: utf-8 -*-
"""fundamental_sync 单元测试。

覆盖：
- run_financial_sync 写全局进度时登记 worker_pid（崩溃后 sync_is_active
  能识别 worker 已死，避免进度残留导致后续同步永久 409 阻塞）
- expected_latest_report_date：按 A 股披露周期判断"今天应披露的最新报告期"
  （财报是季频数据，不应每次全同步都全市场重拉）
"""
import os
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.services.data import fundamental_sync as fs


# ---------- 披露周期判断 ----------

@pytest.mark.parametrize("today,expected", [
    # 一季报(3/31) 截止 4/30；在此之前最新应披露的是去年三季报(9/30)
    (date(2026, 1, 10), date(2025, 9, 30)),
    (date(2026, 4, 29), date(2025, 9, 30)),
    (date(2026, 4, 30), date(2026, 3, 31)),   # 一季报截止日当天
    (date(2026, 6, 15), date(2026, 3, 31)),   # 一季报已截止，中报(8/31) 未到
    (date(2026, 8, 30), date(2026, 3, 31)),
    (date(2026, 8, 31), date(2026, 6, 30)),   # 中报截止日当天
    (date(2026, 9, 1), date(2026, 6, 30)),
    (date(2026, 10, 30), date(2026, 6, 30)),
    (date(2026, 10, 31), date(2026, 9, 30)),  # 三季报截止日当天
    (date(2026, 11, 1), date(2026, 9, 30)),
    (date(2026, 12, 31), date(2026, 9, 30)),
])
def test_expected_latest_report_date(today, expected):
    assert fs.expected_latest_report_date(today) == expected


def test_expected_latest_report_date_defaults_today():
    """不传 today 时用今天（与 date.today() 一致）。"""
    assert fs.expected_latest_report_date() == fs.expected_latest_report_date(date.today())


@pytest.mark.asyncio
async def test_run_financial_sync_registers_worker_pid():
    """broadcast 模式写进度时必须 set_worker_pid(os.getpid())。"""
    with patch("app.services.data.sync_progress.init_progress") as mock_init, \
         patch("app.services.data.sync_progress.set_worker_pid") as mock_setpid, \
         patch("app.services.data.sync_progress.finish_progress"), \
         patch("app.services.data.sync_progress.clear_progress"), \
         patch("asyncio.sleep", new=AsyncMock()), \
         patch.object(fs, "fetch_all_financial", new=AsyncMock(return_value=(0, 0))), \
         patch.object(fs, "broadcast_financial_to_bins", new=AsyncMock(return_value=0)):
        result = await fs.run_financial_sync(broadcast=True, codes=[])

    assert result["ok"] is True
    mock_init.assert_called_once_with("fundamental", "fundamental", writes_bins=True, kind="fundamental")
    mock_setpid.assert_called_once_with(os.getpid())


@pytest.mark.asyncio
async def test_run_financial_sync_fetch_only_does_not_register_pid_when_busy():
    """fetch-only 且已有活跃同步时不写全局进度、不登记 pid（避免覆盖回填进度）。"""
    with patch("app.services.data.sync_progress.sync_is_active", return_value=True), \
         patch("app.services.data.sync_progress.init_progress") as mock_init, \
         patch("app.services.data.sync_progress.set_worker_pid") as mock_setpid, \
         patch.object(fs, "fetch_all_financial", new=AsyncMock(return_value=(0, 0))):
        result = await fs.run_financial_sync(broadcast=False, codes=[])

    assert result["ok"] is True
    mock_init.assert_not_called()
    mock_setpid.assert_not_called()


@pytest.mark.asyncio
async def test_run_financial_sync_progress_cb_does_not_touch_global_progress():
    """并行模式（progress_cb 传入）不 init/finish/clear 全局进度，只走回调。

    一键全同步并行执行宏观/财报/外盘时，若各阶段各自操作共享进度文件，
    会互相覆盖造成竞态——必须统一由 full_sync 管理。
    """
    reports = []

    def _cb(i, n, msg):
        reports.append((i, n, msg))

    with patch("app.services.data.sync_progress.sync_is_active", return_value=True), \
         patch("app.services.data.sync_progress.init_progress") as mock_init, \
         patch("app.services.data.sync_progress.set_worker_pid") as mock_setpid, \
         patch("app.services.data.sync_progress.finish_progress") as mock_finish, \
         patch("app.services.data.sync_progress.clear_progress") as mock_clear, \
         patch.object(fs, "fetch_all_financial",
                      new=AsyncMock(side_effect=lambda codes, progress_cb=None: (
                          progress_cb(1, 10, "拉取中...") or (0, 0)))), \
         patch.object(fs, "broadcast_financial_to_bins", new=AsyncMock(return_value=0)):
        result = await fs.run_financial_sync(broadcast=True, codes=[], progress_cb=_cb)

    assert result["ok"] is True
    assert reports  # 走回调上报了进度
    mock_init.assert_not_called()
    mock_setpid.assert_not_called()
    mock_finish.assert_not_called()
    mock_clear.assert_not_called()
