"""新增子进程 worker（eval/strategy/validation/decay）的测试。

覆盖不依赖 qlib/回测/数据库实体的轻量逻辑：
- pid 生命周期（写/清理/存活判定/陈旧 pid 自愈）
- validation_worker 状态/报告文件往返
- spawn 命令构造（mock subprocess.Popen）
- strategy_worker 任务函数派发（mock run_strategy_backtest / run_param_sweep + async_session）
"""
import asyncio
import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core import database as database_mod


def run(coro):
    # 用 asyncio.run 而非 get_event_loop().run_until_complete：
    # 全量套件中其他测试可能已关闭/替换事件循环，get_event_loop 会抛
    # "no current event loop"（与 test_mining_worker 一致）
    return asyncio.run(coro)


# ---------------- pid 生命周期 ----------------

def test_strategy_worker_pid_lifecycle(monkeypatch, tmp_path):
    from app.services.strategy import strategy_worker as mod
    monkeypatch.setattr(mod, "PID_DIR", str(tmp_path))
    mod.write_worker_pid("backtest", 1)
    assert mod.is_task_running("backtest", 1) is True  # 本进程 pid 必然存活
    mod.clear_worker_pid("backtest", 1)
    assert mod.is_task_running("backtest", 1) is False


def test_strategy_worker_stale_pid_cleared(monkeypatch, tmp_path):
    from app.services.strategy import strategy_worker as mod
    monkeypatch.setattr(mod, "PID_DIR", str(tmp_path))
    # 写入一个必然不存在的 pid → is_task_running 应返回 False 并清理陈旧文件
    (tmp_path / "backtest_2.pid").write_text("99999999")
    assert mod.is_task_running("backtest", 2) is False
    assert not (tmp_path / "backtest_2.pid").exists()


def test_eval_worker_pid_lifecycle(monkeypatch, tmp_path):
    from app.services.factor import eval_worker as mod
    monkeypatch.setattr(mod, "PID_DIR", str(tmp_path))
    mod.write_worker_pid(10)
    assert mod.is_factor_eval_running(10) is True
    mod.clear_worker_pid(10)
    assert mod.is_factor_eval_running(10) is False


def test_decay_worker_pid_lifecycle(monkeypatch, tmp_path):
    from app.services.quant import factor_monitor_worker as mod
    monkeypatch.setattr(mod, "PID_FILE", str(tmp_path / "decay.pid"))
    mod.write_worker_pid()
    assert mod.is_decay_check_running() is True
    mod.clear_worker_pid()
    assert mod.is_decay_check_running() is False


# ---------------- validation_worker：状态/报告文件往返 ----------------

def test_validation_worker_status_roundtrip(monkeypatch, tmp_path):
    from app.services.data import validation_worker as mod
    status_file = tmp_path / "validation_status.json"
    report_file = tmp_path / "validation_report.json"
    monkeypatch.setattr(mod, "PID_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "STATUS_FILE", str(status_file))
    monkeypatch.setattr(mod, "REPORT_FILE", str(report_file))

    mod.write_status({"status": "running", "started_at": "2026-08-10T00:00:00"})
    assert mod.read_status()["status"] == "running"

    mod.write_status({"status": "done", "started_at": "2026-08-10T00:00:00", "finished_at": "2026-08-10T00:00:10"})
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump({"ok": True, "summary": "数据完整"}, f)
    assert mod.read_status()["status"] == "done"
    assert mod.read_report()["summary"] == "数据完整"


def test_validation_worker_status_missing_returns_idle(monkeypatch, tmp_path):
    from app.services.data import validation_worker as mod
    # _paths() 只在 PID_DIR 为 None 时重置路径，因此也要 patch PID_DIR
    monkeypatch.setattr(mod, "PID_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "STATUS_FILE", str(tmp_path / "nope.json"))
    monkeypatch.setattr(mod, "REPORT_FILE", str(tmp_path / "nope_report.json"))
    assert mod.read_status() == {"status": "idle"}
    assert mod.read_report() is None


# ---------------- spawn 命令构造 ----------------

def test_spawn_strategy_worker_command():
    from app.services.strategy import strategy_worker as mod
    with patch.object(mod.subprocess, "Popen") as mock_popen:
        mod.spawn_strategy_worker("backtest", 7, {"start": "2023-01-01", "backend": "qlib"})
    cmd = mock_popen.call_args.kwargs["args"] if "args" in mock_popen.call_args.kwargs else mock_popen.call_args[0][0]
    joined = " ".join(cmd)
    assert "app.services.strategy.strategy_worker" in joined
    assert "--kind backtest" in joined
    assert "--strategy-id 7" in joined
    assert '"backend": "qlib"' in joined
    assert mock_popen.call_args.kwargs["start_new_session"] is True


def test_spawn_validation_worker_command():
    from app.services.data import validation_worker as mod
    with patch.object(mod.subprocess, "Popen") as mock_popen:
        mod.spawn_validation_worker("all")
    cmd = mock_popen.call_args.kwargs["args"] if "args" in mock_popen.call_args.kwargs else mock_popen.call_args[0][0]
    assert "validation_worker" in " ".join(cmd)
    assert "--universe" in cmd and "all" in cmd


# ---------------- strategy_worker 任务派发 ----------------

class FakeSession:
    """最小 async_session 桩：支持 async with / get / commit。"""

    def __init__(self, obj=None):
        self._obj = obj

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, model, pk):
        return self._obj

    async def commit(self):
        pass


def test_backtest_task_success_resets_strategy_status(monkeypatch):
    from app.services.strategy import strategy_worker as mod
    strategy = SimpleNamespace(status="backtest_failed",
                               description="旧描述\n[回测失败] 上次失败")
    session = FakeSession(strategy)
    monkeypatch.setattr(database_mod, "async_session", lambda: session)
    run_backtest = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr("app.services.strategy.manager.run_strategy_backtest", run_backtest)

    run(mod.run_backtest_task(1, {"start": "2023-01-01", "end": "2024-12-31",
                                  "backend": "vbt", "universe": "csi300"}))

    run_backtest.assert_awaited_once()
    kwargs = run_backtest.await_args
    assert kwargs.args[0] == 1
    assert kwargs.kwargs["backend"] == "vbt"
    assert kwargs.kwargs["universe"] == "csi300"
    # 成功后策略状态重置为 active、清除失败标记
    assert strategy.status == "active"
    assert "[回测失败]" not in strategy.description


def test_backtest_task_failure_marks_strategy(monkeypatch):
    from app.services.strategy import strategy_worker as mod
    strategy = SimpleNamespace(status="active", description="")
    session = FakeSession(strategy)
    monkeypatch.setattr(database_mod, "async_session", lambda: session)
    run_backtest = AsyncMock(side_effect=ValueError("qlib 越界"))
    monkeypatch.setattr("app.services.strategy.manager.run_strategy_backtest", run_backtest)

    with patch.object(sys, "exit", side_effect=SystemExit(1)):
        with pytest.raises(SystemExit):
            run(mod.run_backtest_task(1, {"backend": "qlib"}))

    assert strategy.status == "backtest_failed"
    assert "[回测失败] qlib 越界" in strategy.description


def test_param_sweep_task_success_writes_task_result(monkeypatch):
    from app.services.strategy import strategy_worker as mod
    task_result = SimpleNamespace(status="running", payload=None, error=None)
    session = FakeSession(task_result)
    monkeypatch.setattr(database_mod, "async_session", lambda: session)
    sweep = AsyncMock(return_value=[{"topk": 10}, {"topk": 20}])
    monkeypatch.setattr("app.services.strategy.param_sweep.run_param_sweep", sweep)

    run(mod.run_param_sweep_task(1, {"task_result_id": 5, "topk_list": [10, 20],
                                     "rebalance_list": ["day"], "start": "2023-01-01", "end": "2024-12-31"}))

    sweep.assert_awaited_once()
    assert task_result.status == "done"
    assert json.loads(task_result.payload)[0]["topk"] == 10


def test_param_sweep_task_failure_records_error(monkeypatch):
    from app.services.strategy import strategy_worker as mod
    task_result = SimpleNamespace(status="running", payload=None, error=None)
    session = FakeSession(task_result)
    monkeypatch.setattr(database_mod, "async_session", lambda: session)
    sweep = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr("app.services.strategy.param_sweep.run_param_sweep", sweep)

    with patch.object(sys, "exit", side_effect=SystemExit(1)):
        with pytest.raises(SystemExit):
            run(mod.run_param_sweep_task(1, {"task_result_id": 5}))

    assert task_result.status == "failed"
    assert "boom" in task_result.error
