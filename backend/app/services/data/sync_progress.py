"""同步进度跟踪：纯内存模式。

设计：
- 单实例内存缓存：get_progress() 快速读取
- 线程安全：threading.Lock 保护并发更新
- 函数签名与原 Redis 版本兼容，调用方无需改动
"""
import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import AsyncGenerator, Optional

logger = logging.getLogger(__name__)


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
    # 独立 worker 子进程的 PID：用于 web 进程检测同步是否真的在跑（避免残留 syncing 僵尸）
    worker_pid: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _progress_file() -> str:
    """共享进度文件路径（独立 worker 子进程与 web 进程之间的进度桥梁）。

    放在项目 data/ 目录下，跨进程可读写。
    """
    try:
        from app.core.config import settings
        base = settings.PROJECT_ROOT / "data"
    except Exception:
        base = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "data")
    return os.path.join(str(base), "sync_progress.json")


def _read_progress_file() -> Optional[dict]:
    path = _progress_file()
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _write_progress_file(progress: Optional[dict]) -> None:
    path = _progress_file()
    try:
        if progress is None:
            if os.path.exists(path):
                os.remove(path)
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(progress, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:
        logger.debug("写入进度文件失败: %s", e)


def _pid_alive(pid) -> bool:
    """判断 PID 对应的进程是否存活（僵尸进程视为已死）。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 存在但无权限探测（如其他用户进程），保守视为存活
    except OSError:
        return False
    # kill(pid,0) 成功不代表进程真的在跑：僵尸进程（defunct）仍保留进程表项。
    # 僵尸已不执行任何代码，若 worker 异常退出且无人 waitpid 回收，残留的进度
    # 文件会让 sync_is_active 长期误判"正在同步"而阻塞后续同步/补齐（409）。
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="ascii") as f:
            state = f.read().split()[2]
        return state != "Z"
    except (OSError, IndexError, ValueError):
        return True  # /proc 不可用或解析失败时退化为仅靠 kill 探测


class SyncProgressManager:
    """纯内存进度管理器（线程安全）。

    单实例使用，进度存储在进程内存中。
    若未来需要多实例进度共享，可重新引入 Redis Pub/Sub。
    """

    def __init__(self, **_kwargs):
        # 兼容旧调用签名 SyncProgressManager(redis_url=..., enabled=...)，参数忽略
        self._lock = threading.Lock()
        self._progress: Optional[SyncProgress] = None

    def init_progress(self, universe: str, data_source: str, total_mb: float = 0) -> None:
        """初始化进度跟踪"""
        with self._lock:
            # 保留仍存活的 worker_pid：独立 worker 子进程内任务可能再次
            # init_progress（如 run_repair / run_baostock_backfill），若丢失 pid，
            # 进程被杀后 sync_is_active 将无法识别僵尸任务而长期阻塞重触发。
            alive_pid = None
            prev = self._progress
            if prev is not None and _pid_alive(prev.worker_pid):
                alive_pid = prev.worker_pid
            elif prev is None:
                f = _read_progress_file()
                if f and _pid_alive(f.get("worker_pid")):
                    alive_pid = f["worker_pid"]
            self._progress = SyncProgress(
                universe=universe,
                data_source=data_source,
                status="downloading",
                total_mb=total_mb,
                started_at=datetime.now().isoformat(),
            )
            if alive_pid:
                self._progress.worker_pid = int(alive_pid)
            _write_progress_file(self._progress.to_dict())

    def update_progress(
        self,
        pct: float = None,
        downloaded_mb: float = None,
        speed_mbps: float = None,
        status: str = None,
        message: str = None,
        error: str = None,
    ) -> None:
        """更新进度"""
        with self._lock:
            if self._progress is None:
                return
            if pct is not None:
                self._progress.progress_pct = round(pct, 1)
            if downloaded_mb is not None:
                self._progress.downloaded_mb = round(downloaded_mb, 1)
            if speed_mbps is not None:
                self._progress.speed_mbps = round(speed_mbps, 1)
            if status is not None:
                self._progress.status = status
            if message is not None:
                self._progress.message = message
            if error is not None:
                self._progress.error = error
            _write_progress_file(self._progress.to_dict())

    def finish_progress(self, success: bool, error: str = None) -> None:
        """完成进度"""
        with self._lock:
            if self._progress is None:
                return
            self._progress.status = "done" if success else "failed"
            self._progress.progress_pct = 100.0 if success else self._progress.progress_pct
            self._progress.error = error
            _write_progress_file(self._progress.to_dict())

    def get_progress(self) -> Optional[dict]:
        """获取当前进度。

        优先取进程内存；内存为空时回退到共享进度文件（独立 worker 子进程的进度）。
        """
        with self._lock:
            if self._progress is not None:
                return self._progress.to_dict()
            file_prog = _read_progress_file()
            if file_prog is not None:
                return file_prog
            return None

    def clear_progress(self) -> None:
        """清除进度"""
        with self._lock:
            self._progress = None
            _write_progress_file(None)

    async def subscribe_progress(self) -> AsyncGenerator[dict, None]:
        """订阅进度更新（内存轮询模式）。

        每 0.5s 轮询一次内存进度，yield 进度字典。
        供 WebSocket 等长期连接使用。完成后自动结束。
        """
        last_pct = -1
        last_status = None
        while True:
            progress = self.get_progress()
            if progress is None:
                await asyncio.sleep(0.5)
                continue
            # 只在进度变化时 yield，避免重复推送
            if progress["progress_pct"] != last_pct or progress["status"] != last_status:
                last_pct = progress["progress_pct"]
                last_status = progress["status"]
                yield progress
            # done/failed 后结束订阅
            if progress["status"] in ("done", "failed"):
                break
            await asyncio.sleep(0.5)

    async def close(self) -> None:
        """关闭管理器（内存模式无资源需释放，空操作）。"""
        pass


# -- 全局单例 --

_manager: Optional[SyncProgressManager] = None


def _get_manager() -> SyncProgressManager:
    """获取或初始化全局进度管理器（惰性初始化）。"""
    global _manager
    if _manager is None:
        _manager = SyncProgressManager()
    return _manager


# -- 模块级函数（向后兼容，保持原有签名） --


def init_progress(universe: str, data_source: str, total_mb: float = 0) -> None:
    """初始化进度跟踪"""
    _get_manager().init_progress(universe, data_source, total_mb)


def update_progress(
    pct: float = None,
    downloaded_mb: float = None,
    speed_mbps: float = None,
    status: str = None,
    message: str = None,
    error: str = None,
) -> None:
    """更新进度"""
    _get_manager().update_progress(
        pct=pct,
        downloaded_mb=downloaded_mb,
        speed_mbps=speed_mbps,
        status=status,
        message=message,
        error=error,
    )


def finish_progress(success: bool, error: str = None) -> None:
    """完成进度"""
    _get_manager().finish_progress(success, error)


def get_progress() -> Optional[dict]:
    """获取当前进度"""
    return _get_manager().get_progress()


def set_worker_pid(pid: int) -> None:
    """记录运行在独立子进程里的 worker PID（供 web 进程检测存活）。"""
    manager = _get_manager()
    with manager._lock:
        if manager._progress is None:
            return
        manager._progress.worker_pid = int(pid)
        _write_progress_file(manager._progress.to_dict())


def sync_is_active() -> bool:
    """判断当前是否真的有一个活跃的同步任务在跑。

    - 内存进度存在且状态非终态 → 活跃
    - 共享文件进度存在：
        - 状态为终态(done/failed) → 不活跃（等被 clear）
        - 有 worker_pid 且存活 → 活跃
        - 有 worker_pid 但已死（残留文件）→ 不活跃，允许重新触发并提示清理
        - 无 worker_pid 且状态非终态 → 保守视为活跃（历史 in-process 路径）
    """
    progress = get_progress()
    if progress is None:
        return False
    if progress.get("status") in ("done", "failed", "idle", None):
        return False
    pid = progress.get("worker_pid")
    if pid and not _pid_alive(pid):
        # worker 已死，残留进度文件 → 不活跃，允许重新触发
        return False
    return True


def clear_progress() -> None:
    """清除进度"""
    _get_manager().clear_progress()


async def subscribe_progress() -> AsyncGenerator[dict, None]:
    """订阅进度更新（异步生成器，内存轮询模式）。"""
    async for msg in _get_manager().subscribe_progress():
        yield msg


async def close_progress_manager() -> None:
    """关闭进度管理器（内存模式无资源需释放，空操作）。"""
    global _manager
    if _manager is not None:
        await _manager.close()
        _manager = None
