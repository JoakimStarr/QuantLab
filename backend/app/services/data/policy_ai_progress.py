"""policy_ai 后台任务进度：共享进度文件（worker 子进程 ↔ web 进程跨进程通信）。

与 sync_progress 的关系：policy_ai 是独立的剧本式 worker（不写 qlib bin、不占爬取锁），
专门用小文件承载其逐日进度，避免与数据同步的全局进度文件混在一起。

设计：worker 子进程写、web 进程只读。终态 status 为 running/done/failed，
前端以此判断「本次任务是否真正结束」（状态栏的 ai_pending 是全历史口径，
与单次回填窗口不符，不能作为任务完成信号）。
"""
import json
import logging
import os
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

_path: str | None = None
_started_at: str | None = None
_lock = threading.Lock()


def _progress_file() -> str:
    """共享进度文件路径（与 sync_progress 同样放在 data/ 下）。"""
    global _path
    if _path is None:
        try:
            from app.core.config import settings
            _path = str(settings.PROJECT_ROOT / "data" / "policy_ai_progress.json")
        except Exception:
            _path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..",
                                 "data", "policy_ai_progress.json")
    return _path


def _read() -> dict | None:
    try:
        with open(_progress_file(), encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _write(obj: dict) -> None:
    try:
        path = _progress_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:
        logger.debug("写入 policy_ai 进度失败: %s", e)


def start(total: int) -> None:
    """任务开始：记录总共待处理天数。"""
    global _started_at
    _started_at = datetime.now().isoformat()
    with _lock:
        _write({"status": "running", "total": int(total), "done": 0, "failed": 0,
                "started_at": _started_at})


def update(done: int, failed: int, total: int) -> None:
    """进度更新：已完成（成功+失败）/ 总天数。"""
    with _lock:
        _write({"status": "running", "total": int(total), "done": int(done),
                "failed": int(failed), "started_at": _started_at})


def finish(success: bool, error: str | None = None,
           done: int = 0, failed: int = 0, total: int = 0) -> None:
    """任务结束：写终态 status（done/failed），前端据此停止轮询。"""
    global _started_at
    with _lock:
        _write({"status": "done" if success else "failed",
                "total": int(total), "done": int(done), "failed": int(failed),
                "started_at": _started_at, "error": error})
    _started_at = None


def get_progress() -> dict | None:
    """读取当前进度（web 进程只读调用）。"""
    return _read()


def clear() -> None:
    with _lock:
        try:
            if os.path.exists(_progress_file()):
                os.remove(_progress_file())
        except Exception as e:  # noqa: BLE001
            logger.debug("清除 policy_ai 进度失败: %s", e)
