"""同步进度跟踪：纯内存模式。

设计：
- 单实例内存缓存：get_progress() 快速读取
- 线程安全：threading.Lock 保护并发更新
- 函数签名与原 Redis 版本兼容，调用方无需改动
"""
import asyncio
import logging
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

    def to_dict(self) -> dict:
        return asdict(self)


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
            self._progress = SyncProgress(
                universe=universe,
                data_source=data_source,
                status="downloading",
                total_mb=total_mb,
                started_at=datetime.now().isoformat(),
            )

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

    def finish_progress(self, success: bool, error: str = None) -> None:
        """完成进度"""
        with self._lock:
            if self._progress is None:
                return
            self._progress.status = "done" if success else "failed"
            self._progress.progress_pct = 100.0 if success else self._progress.progress_pct
            self._progress.error = error

    def get_progress(self) -> Optional[dict]:
        """获取当前进度"""
        with self._lock:
            if self._progress is None:
                return None
            return self._progress.to_dict()

    def clear_progress(self) -> None:
        """清除进度"""
        with self._lock:
            self._progress = None

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
