from contextlib import asynccontextmanager
import os
import logging
from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from app.core.database import init_db, async_session
from app.core.logging_config import setup_logging
from app.core.middleware import setup_middleware
from app.core.errors import AppError, app_error_handler, general_error_handler
from app.core.scheduler import start_scheduler, stop_scheduler
from app.core.recovery import recover_stale_sync, recover_stale_mining
from app.api.router import api_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    await init_db()
    await recover_stale_sync()
    await recover_stale_mining()
    await start_scheduler()
    yield
    await stop_scheduler()


app = FastAPI(title="QuantLab", version="2.0.0", lifespan=lifespan)

setup_middleware(app)
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(Exception, general_error_handler)
app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
async def health():
    status = "ok"
    checks = {}
    # 数据库检查
    try:
        from app.core.database import engine
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"
        status = "degraded"
    # qlib 检查
    try:
        from app.services.quant.qlib_init import is_qlib_available
        qlib_ok = await is_qlib_available()
        checks["qlib"] = "ok" if qlib_ok else "not_available"
    except Exception as e:
        checks["qlib"] = f"error: {e}"
    return {
        "status": status,
        "timestamp": datetime.now().isoformat(),
        "version": "2.0.0",
        "checks": checks,
    }


# 前端静态文件目录（容器启动时通过 volume 挂载前端 dist）
_static_dir = os.environ.get("STATIC_DIR", "static")


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    """SPA 路由兜底：优先返回静态文件，否则返回 index.html 交给前端路由"""
    # 无静态目录时回退到 API 文档
    if not os.path.isdir(_static_dir):
        return RedirectResponse(url="/docs")
    # 1. 尝试返回实际静态文件（assets、favicon 等）
    if full_path:
        file_path = os.path.normpath(os.path.join(_static_dir, full_path))
        abs_static = os.path.abspath(_static_dir)
        abs_file = os.path.abspath(file_path)
        # 安全检查：防止路径穿越
        if abs_file.startswith(abs_static + os.sep) and os.path.isfile(file_path):
            return FileResponse(file_path)
    # 2. SPA 兜底：返回 index.html 交给前端路由处理
    index_path = os.path.join(_static_dir, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    # 3. 无 index.html 时回退到 API 文档
    return RedirectResponse(url="/docs")


# WebSocket 端点（添加13: WebSocket 实时推送）
from app.core.websocket_manager import ws_manager
from fastapi import WebSocket

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket 实时推送：同步进度、回测进度等"""
    await ws_manager.connect(ws)
    try:
        while True:
            # 保持连接，接收心跳
            data = await ws.receive_text()
            if data == "ping":
                await ws_manager.send_to(ws, "pong", {"timestamp": datetime.now().isoformat()})
    except Exception:
        pass
    finally:
        await ws_manager.disconnect(ws)
