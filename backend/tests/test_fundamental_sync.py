# -*- coding: utf-8 -*-
"""fundamental_sync 单元测试。

覆盖：
- run_financial_sync 写全局进度时登记 worker_pid（崩溃后 sync_is_active
  能识别 worker 已死，避免进度残留导致后续同步永久 409 阻塞）
"""
import os
from unittest.mock import AsyncMock, patch

import pytest

from app.services.data import fundamental_sync as fs


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
    mock_init.assert_called_once_with("fundamental", "fundamental", writes_bins=True)
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
