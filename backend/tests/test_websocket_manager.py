"""WebSocketManager 心跳超时清理测试。"""

import time
from unittest.mock import AsyncMock

import pytest

from app.core.websocket_manager import (
    WebSocketManager,
)


class FakeWebSocket:
    """轻量 WebSocket 测试替身：跟踪 send_text/close 调用。"""

    def __init__(self):
        self.accepted = False
        self.sent: list[str] = []
        self.close_code = None
        self.close_reason = None

    async def accept(self):
        self.accepted = True

    async def send_text(self, msg: str):
        self.sent.append(msg)

    async def close(self, code: int = 1000, reason: str = ""):
        self.close_code = code
        self.close_reason = reason


@pytest.mark.asyncio
async def test_connect_accepts_and_tracks():
    mgr = WebSocketManager(heartbeat_timeout=60.0, reaper_interval=30.0)
    ws = FakeWebSocket()
    await mgr.connect(ws)
    assert ws.accepted is True
    assert len(mgr._connections) == 1
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_disconnect_removes_connection():
    mgr = WebSocketManager(heartbeat_timeout=60.0, reaper_interval=30.0)
    ws = FakeWebSocket()
    await mgr.connect(ws)
    assert len(mgr._connections) == 1
    await mgr.disconnect(ws)
    assert len(mgr._connections) == 0
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_broadcast_sends_to_all():
    mgr = WebSocketManager(heartbeat_timeout=60.0, reaper_interval=30.0)
    ws1, ws2 = FakeWebSocket(), FakeWebSocket()
    await mgr.connect(ws1)
    await mgr.connect(ws2)
    await mgr.broadcast("test", {"x": 1})
    assert len(ws1.sent) == 1
    assert len(ws2.sent) == 1
    assert '"type": "test"' in ws1.sent[0]
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_broadcast_handles_dead_connection():
    mgr = WebSocketManager(heartbeat_timeout=60.0, reaper_interval=30.0)
    ws_ok = FakeWebSocket()
    ws_dead = FakeWebSocket()
    ws_dead.send_text = AsyncMock(side_effect=RuntimeError("client gone"))
    await mgr.connect(ws_ok)
    await mgr.connect(ws_dead)
    await mgr.broadcast("evt", {})
    # 死连接被移除
    assert len(mgr._connections) == 1
    assert ws_ok.sent  # 活的连接收到消息
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_send_to_single():
    mgr = WebSocketManager(heartbeat_timeout=60.0, reaper_interval=30.0)
    ws = FakeWebSocket()
    await mgr.connect(ws)
    await mgr.send_to(ws, "hello", {"k": "v"})
    assert len(ws.sent) == 1
    assert '"type": "hello"' in ws.sent[0]
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_update_heartbeat():
    mgr = WebSocketManager(heartbeat_timeout=60.0, reaper_interval=30.0)
    ws = FakeWebSocket()
    await mgr.connect(ws)
    conn = next(iter(mgr._connections))
    original = conn.last_pong_at
    time.sleep(0.01)
    mgr.update_heartbeat(ws)
    assert conn.last_pong_at > original
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_reaper_clears_stale_connections():
    """reaper 应关闭并清理心跳超时的连接。"""
    mgr = WebSocketManager(heartbeat_timeout=0.1, reaper_interval=10.0)
    ws1, ws2 = FakeWebSocket(), FakeWebSocket()
    await mgr.connect(ws1)
    await mgr.connect(ws2)
    # 让 ws1 持续保活，ws2 模拟超时
    mgr.update_heartbeat(ws1)
    # 将 ws2 的 last_pong_at 强制改成过去
    for conn in mgr._connections:
        if conn.ws is ws2:
            conn.last_pong_at = time.monotonic() - 1.0
    await mgr._reap_stale_once()
    assert ws2.close_code == 4408
    assert ws1.close_code is None
    assert len(mgr._connections) == 1
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_reaper_loop_runs_and_cancels():
    mgr = WebSocketManager(heartbeat_timeout=60.0, reaper_interval=0.05)
    ws = FakeWebSocket()
    await mgr.connect(ws)
    # reaper 任务应已启动
    assert mgr._reaper_task is not None
    await mgr.shutdown()
    assert mgr._reaper_task is None or mgr._reaper_task.done()
