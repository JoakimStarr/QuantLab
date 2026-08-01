"""同步进度跟踪：Redis Pub/Sub 替换内存字典，支持多实例进度共享。

设计：
- 本地内存缓存：保证 get_progress() 快速读取，单实例向后兼容
- Redis Pub/Sub：进度更新时发布到 "sync_progress" 频道，多实例可订阅实时进度
- 自动降级：Redis 不可用时降级为纯内存模式，不中断业务
- 同步/异步兼容：update_progress() 等同步函数同时更新本地缓存并异步发布 Redis
"""
import asyncio
import json
import logging
import threading
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import AsyncGenerator, Optional

logger = logging.getLogger(__name__)

# Redis Pub/Sub 频道名
_SYNC_PROGRESS_CHANNEL = "sync_progress"


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
    """Redis Pub/Sub 进度管理器，带本地内存缓存兜底。

    - 当 Redis 启用时，每次进度更新同时发布到 Redis 频道
    - 当 Redis 不可用时自动降级为纯内存模式，确保单实例向后兼容
    - 通过 subscribe_progress() 异步生成器订阅实时进度
    """

    def __init__(self, redis_url: str = "", enabled: bool = False):
        self._redis_url = redis_url
        self._enabled = enabled
        self._lock = threading.Lock()
        self._progress: Optional[SyncProgress] = None
        # 同步创建 Redis 客户端（延迟连接，首次命令自动建连）
        self._redis = self._init_redis()

    def _init_redis(self):
        """同步创建 Redis 异步客户端。

        redis.asyncio.from_url() 返回的是延迟连接客户端，
        实际 TCP 连接在首次命令时建立，因此可以在同步上下文中安全创建。
        """
        if not self._enabled or not self._redis_url:
            return None
        try:
            import redis.asyncio as redis
            client = redis.from_url(
                self._redis_url,
                socket_connect_timeout=3,
                socket_timeout=3,
            )
            logger.info("Redis 客户端已创建（延迟连接）: %s", self._redis_url)
            return client
        except Exception as e:
            logger.warning("Redis 客户端创建失败，降级为内存模式: %s", e)
            return None

    async def publish_progress(self, progress_dict: dict) -> None:
        """发布进度到 Redis 频道。"""
        if self._redis is None:
            return
        try:
            await self._redis.publish(
                _SYNC_PROGRESS_CHANNEL,
                json.dumps(progress_dict, ensure_ascii=False, default=str),
            )
        except Exception as e:
            logger.debug("Redis publish 失败: %s", e)
            # 连接级错误（如 Redis 未启动）自动降级，后续不再重试
            self._redis = None

    async def subscribe_progress(self) -> AsyncGenerator[dict, None]:
        """订阅进度更新，异步生成器逐条 yield 进度字典。

        Redis 不可用时直接返回（不抛异常），调用方无需额外处理。

        Usage:
            async for progress in manager.subscribe_progress():
                print(progress["status"], progress["progress_pct"])
        """
        if self._redis is None:
            return
        pubsub = self._redis.pubsub()
        try:
            await pubsub.subscribe(_SYNC_PROGRESS_CHANNEL)
        except Exception as e:
            logger.warning("Redis 订阅失败，跳过进度订阅: %s", e)
            await pubsub.close()
            return
        try:
            async for msg in pubsub.listen():
                if msg["type"] == "message":
                    try:
                        yield json.loads(msg["data"])
                    except json.JSONDecodeError:
                        continue
        finally:
            await pubsub.unsubscribe(_SYNC_PROGRESS_CHANNEL)
            await pubsub.close()

    # -- 同步接口（向后兼容） --

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
        self._try_publish()

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
        self._try_publish()

    def finish_progress(self, success: bool, error: str = None) -> None:
        """完成进度"""
        with self._lock:
            if self._progress is None:
                return
            self._progress.status = "done" if success else "failed"
            self._progress.progress_pct = 100.0 if success else self._progress.progress_pct
            self._progress.error = error
        self._try_publish()

    def get_progress(self) -> Optional[dict]:
        """获取当前进度（从本地缓存读取，快速响应）"""
        with self._lock:
            if self._progress is None:
                return None
            return self._progress.to_dict()

    def clear_progress(self) -> None:
        """清除进度"""
        with self._lock:
            self._progress = None

    # -- 内部 --

    def _try_publish(self) -> None:
        """尝试异步发布进度到 Redis（fire-and-forget）。

        从同步上下文调用时，若当前有运行中的事件循环则创建 task，
        否则跳过 Redis 发布（仅更新本地缓存）。
        """
        if self._redis is None:
            return
        with self._lock:
            if self._progress is None:
                return
            progress_dict = self._progress.to_dict()
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(self.publish_progress(progress_dict))
        except RuntimeError:
            pass  # 无事件循环，跳过 Redis 发布

    async def close(self) -> None:
        """关闭 Redis 连接（应用关闭时调用）。"""
        if self._redis is not None:
            try:
                await self._redis.close()
            except Exception:
                pass
            self._redis = None


# -- 全局单例 --

_manager: Optional[SyncProgressManager] = None


def _get_manager() -> SyncProgressManager:
    """获取或初始化全局进度管理器（惰性初始化，从 settings 读取 Redis 配置）。"""
    global _manager
    if _manager is None:
        try:
            from app.core.config import settings
            _manager = SyncProgressManager(
                redis_url=settings.redis.url,
                enabled=settings.redis.enabled,
            )
        except (ImportError, Exception) as e:
            logger.warning("无法加载 settings，使用纯内存模式: %s", e)
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
    """订阅进度更新（异步生成器）。

    供 WebSocket 等长期连接使用，实时接收跨实例进度推送。
    """
    async for msg in _get_manager().subscribe_progress():
        yield msg


async def close_progress_manager() -> None:
    """关闭进度管理器（应用关闭时调用，释放 Redis 连接）。"""
    global _manager
    if _manager is not None:
        await _manager.close()
        _manager = None