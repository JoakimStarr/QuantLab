"""同步进度跟踪：内存中的进度状态，供 API 和 WebSocket 查询"""
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

_lock = threading.Lock()


@dataclass
class SyncProgress:
    universe: str = ""
    data_source: str = ""
    status: str = "idle"  # idle / downloading / extracting / verifying / done / failed
    progress_pct: float = 0.0
    downloaded_mb: float = 0.0
    total_mb: float = 0.0
    speed_mbps: float = 0.0
    started_at: Optional[str] = None
    message: str = ""
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


_progress: Optional[SyncProgress] = None


def init_progress(universe: str, data_source: str, total_mb: float = 0):
    """初始化进度跟踪"""
    global _progress
    with _lock:
        _progress = SyncProgress(
            universe=universe,
            data_source=data_source,
            status="downloading",
            total_mb=total_mb,
            started_at=datetime.now().isoformat(),
        )


def update_progress(pct: float = None, downloaded_mb: float = None,
                    speed_mbps: float = None, status: str = None,
                    message: str = None, error: str = None):
    """更新进度"""
    global _progress
    with _lock:
        if _progress is None:
            return
        if pct is not None:
            _progress.progress_pct = round(pct, 1)
        if downloaded_mb is not None:
            _progress.downloaded_mb = round(downloaded_mb, 1)
        if speed_mbps is not None:
            _progress.speed_mbps = round(speed_mbps, 1)
        if status is not None:
            _progress.status = status
        if message is not None:
            _progress.message = message
        if error is not None:
            _progress.error = error


def finish_progress(success: bool, error: str = None):
    """完成进度"""
    global _progress
    with _lock:
        if _progress is None:
            return
        _progress.status = "done" if success else "failed"
        _progress.progress_pct = 100.0 if success else _progress.progress_pct
        _progress.error = error


def get_progress() -> Optional[dict]:
    """获取当前进度"""
    with _lock:
        if _progress is None:
            return None
        return _progress.to_dict()


def clear_progress():
    """清除进度"""
    global _progress
    with _lock:
        _progress = None
