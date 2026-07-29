"""WebSocket 连接管理器：实时推送同步进度、回测进度等"""
import json
import asyncio
import logging
from typing import Set
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    """管理 WebSocket 连接，支持广播消息"""

    def __init__(self):
        self._connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        """接受新连接"""
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)
        logger.info("WebSocket 连接已建立，当前连接数: %d", len(self._connections))

    async def disconnect(self, ws: WebSocket):
        """断开连接"""
        async with self._lock:
            self._connections.discard(ws)
        logger.info("WebSocket 连接已断开，当前连接数: %d", len(self._connections))

    async def broadcast(self, event_type: str, data: dict):
        """广播消息到所有连接"""
        if not self._connections:
            return
        message = json.dumps({"type": event_type, "data": data}, ensure_ascii=False, default=str)
        dead = set()
        async with self._lock:
            for ws in self._connections:
                try:
                    await ws.send_text(message)
                except Exception as e:
                    logger.debug("发送失败，标记为断开: %s", e)
                    dead.add(ws)
            self._connections -= dead

    async def send_to(self, ws: WebSocket, event_type: str, data: dict):
        """发送消息到单个连接"""
        message = json.dumps({"type": event_type, "data": data}, ensure_ascii=False, default=str)
        try:
            await ws.send_text(message)
        except Exception as e:
            logger.debug("发送失败: %s", e)
            await self.disconnect(ws)


# 全局单例
ws_manager = WebSocketManager()
