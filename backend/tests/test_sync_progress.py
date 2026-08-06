# -*- coding: utf-8 -*-
"""sync_progress 单元测试：calendar_shifting_active（数据同步与回测/挖掘解耦）。

解耦原则：
- 会重塑日历对齐的同步（backfill/repair/full）→ 回测/挖掘必须等待（读到错位 bin）
- 纯追加的同步（eod/etf/indices，bin 原子写）→ 回测/挖掘可并发，互不打扰
"""
from unittest.mock import patch

from app.services.data import sync_progress as sp


def _progress(data_source, writes_bins=True):
    return {
        "status": "downloading", "data_source": data_source,
        "writes_bins": writes_bins, "worker_pid": 12345,
    }


def test_calendar_shifting_blocks_backfill():
    """回填（data_source=baostock）会重塑日历 → 返回 True（应拦截回测/挖掘）。"""
    with patch.object(sp, "sync_is_active", return_value=True), \
         patch.object(sp, "get_progress", return_value=_progress("baostock")):
        assert sp.calendar_shifting_active() is True


def test_calendar_shifting_blocks_repair_and_full():
    """补齐/一键全同步同样可能重塑日历 → True。"""
    for src in ("repair", "full"):
        with patch.object(sp, "sync_is_active", return_value=True), \
             patch.object(sp, "get_progress", return_value=_progress(src)):
            assert sp.calendar_shifting_active() is True, src


def test_calendar_shifting_allows_append_syncs():
    """EOD/ETF/指数等纯追加同步 → False（回测/挖掘可并发执行）。"""
    for src in ("eod", "etf", "indices", "fundamental", "eastmoney"):
        with patch.object(sp, "sync_is_active", return_value=True), \
             patch.object(sp, "get_progress", return_value=_progress(src)):
            assert sp.calendar_shifting_active() is False, src


def test_calendar_shifting_no_active_sync():
    """无活跃同步 → False。"""
    with patch.object(sp, "sync_is_active", return_value=False):
        assert sp.calendar_shifting_active() is False


def test_calendar_shifting_fetch_only_sync():
    """fetch-only 任务（不写 bin）→ False。"""
    with patch.object(sp, "sync_is_active", return_value=True), \
         patch.object(sp, "get_progress", return_value=_progress("fundamental", writes_bins=False)):
        assert sp.calendar_shifting_active() is False
