"""WebSocket 连接管理器：实时推送同步进度、回测进度等。

特性：
- 锁内只 snapshot 连接集合，锁外逐个发送，避免慢/死连接阻塞其它连接
- 心跳超时清理：每个连接记录 last_pong_at，后台 reaper 定期扫描并主动 close
"""

import asyncio
import json
import logging
import time

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# 默认心跳超时（秒）：客户端两次 ping 间隔超过此值即视为死连接
DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 60.0
# 后台 reaper 扫描间隔（秒）
DEFAULT_REAPER_INTERVAL_SECONDS = 30.0


class _Connection:
    """WebSocket 连接 + 心跳状态。"""

    __slots__ = ("ws", "last_pong_at")

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.last_pong_at = time.monotonic()


class WebSocketManager:
    """管理 WebSocket 连接，支持广播与心跳清理。"""

    def __init__(
        self,
        heartbeat_timeout: float = DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
        reaper_interval: float = DEFAULT_REAPER_INTERVAL_SECONDS,
    ):
        self._connections: set[_Connection] = set()
        self._lock = asyncio.Lock()
        self._heartbeat_timeout = heartbeat_timeout
        self._reaper_interval = reaper_interval
        self._reaper_task: asyncio.Task | None = None

    async def connect(self, ws: WebSocket) -> _Connection:
        """接受新连接，返回连接对象供调用方更新心跳。"""
        await ws.accept()
        conn = _Connection(ws)
        async with self._lock:
            self._connections.add(conn)
        logger.info("WebSocket 连接已建立，当前连接数: %d", len(self._connections))
        self._ensure_reaper()
        # Prometheus gauge
        try:
            from app.core.metrics import ws_active_connections

            ws_active_connections.set(len(self._connections))
        except Exception:
            pass
        return conn

    async def disconnect(self, ws: WebSocket) -> None:
        """断开连接（按 ws 引用匹配）。"""
        async with self._lock:
            for conn in list(self._connections):
                if conn.ws is ws:
                    self._connections.discard(conn)
                    break
        logger.info("WebSocket 连接已断开，当前连接数: %d", len(self._connections))
        # Prometheus gauge
        try:
            from app.core.metrics import ws_active_connections

            ws_active_connections.set(len(self._connections))
        except Exception:
            pass

    def update_heartbeat(self, ws: WebSocket) -> None:
        """更新心跳时间戳（轻量，无需加锁，monotonic 写入是原子的）。"""
        for conn in self._connections:
            if conn.ws is ws:
                conn.last_pong_at = time.monotonic()
                return

    async def broadcast(self, event_type: str, data: dict) -> None:
        """广播消息到所有连接。

        锁内只 snapshot 连接集合，锁外逐个发送，避免慢/死连接阻塞其它连接与 connect/disconnect。
        """
        async with self._lock:
            snapshot = list(self._connections)
        if not snapshot:
            return
        message = json.dumps(
            {"type": event_type, "data": data},
            ensure_ascii=False,
            default=str,
        )
        dead: list[WebSocket] = []
        for conn in snapshot:
            try:
                await conn.ws.send_text(message)
            except Exception as e:
                logger.debug("广播发送失败，标记为断开: %s", e)
                dead.append(conn.ws)
        if dead:
            async with self._lock:
                self._connections = {c for c in self._connections if c.ws not in dead}

    async def send_to(self, ws: WebSocket, event_type: str, data: dict) -> None:
        """发送消息到单个连接"""
        message = json.dumps(
            {"type": event_type, "data": data},
            ensure_ascii=False,
            default=str,
        )
        try:
            await ws.send_text(message)
        except Exception as e:
            logger.debug("发送失败: %s", e)
            await self.disconnect(ws)

    def _ensure_reaper(self) -> None:
        """启动后台心跳 reaper 任务（仅启动一次）。"""
        if self._reaper_task is not None and not self._reaper_task.done():
            return
        self._reaper_task = asyncio.create_task(
            self._reap_stale_loop(),
            name="ws-heartbeat-reaper",
        )

    async def _reap_stale_loop(self) -> None:
        """定期清理心跳超时的死连接。"""
        try:
            while True:
                await asyncio.sleep(self._reaper_interval)
                await self._reap_stale_once()
        except asyncio.CancelledError:
            logger.info("WebSocket 心跳 reaper 已停止")
            raise
        except Exception:
            logger.exception("WebSocket 心跳 reaper 异常退出")

    async def _reap_stale_once(self) -> None:
        """扫描并清理一次心跳超时连接。"""
        now = time.monotonic()
        cutoff = now - self._heartbeat_timeout
        stale: list[_Connection] = []
        async with self._lock:
            for conn in self._connections:
                if conn.last_pong_at < cutoff:
                    stale.append(conn)
        if not stale:
            return
        for conn in stale:
            try:
                await conn.ws.close(code=4408, reason="heartbeat timeout")
            except Exception:
                pass
            async with self._lock:
                self._connections.discard(conn)
        logger.warning(
            "WebSocket 心跳 reaper 清理 %d 个超时连接，剩余 %d",
            len(stale),
            len(self._connections),
        )

    async def shutdown(self) -> None:
        """应用关闭时清理 reaper。"""
        if self._reaper_task is not None and not self._reaper_task.done():
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reaper_task = None
        async with self._lock:
            self._connections.clear()


# 全局单例
ws_manager = WebSocketManager()
