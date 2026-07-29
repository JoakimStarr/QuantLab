"""回测状态追踪器（内存版，进程重启后状态丢失）"""
import threading
from datetime import datetime

_lock = threading.Lock()
_status = {}  # strategy_id -> {status, started_at, finished_at, error, progress}


def set_running(strategy_id: int):
    with _lock:
        _status[strategy_id] = {
            "status": "running",
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
            "error": None,
            "progress": 0,
        }


def set_completed(strategy_id: int):
    with _lock:
        if strategy_id not in _status:
            _status[strategy_id] = {}
        _status[strategy_id].update({
            "status": "completed",
            "finished_at": datetime.now().isoformat(),
            "progress": 100,
        })


def set_failed(strategy_id: int, error: str):
    with _lock:
        if strategy_id not in _status:
            _status[strategy_id] = {}
        _status[strategy_id].update({
            "status": "failed",
            "finished_at": datetime.now().isoformat(),
            "error": error[:500],
        })


def get_status(strategy_id: int) -> dict:
    with _lock:
        return _status.get(strategy_id, {"status": "idle"})


def get_all_status() -> dict:
    with _lock:
        return dict(_status)


def clear_status(strategy_id: int):
    with _lock:
        _status.pop(strategy_id, None)
