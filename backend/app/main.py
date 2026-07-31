from contextlib import asynccontextmanager
import os
import logging
from datetime import datetime
from fastapi import FastAPI, WebSocket, Query
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi import HTTPException
from sqlalchemy import select, text
from app.core.database import init_db, async_session
from app.core.logging_config import setup_logging
from app.core.middleware import setup_middleware
from app.core.errors import (
    AppError, app_error_handler, general_error_handler,
    validation_error_handler, http_exception_handler,
)
from app.core.scheduler import start_scheduler, stop_scheduler
from app.core.recovery import recover_stale_sync, recover_stale_mining
from app.core.auth import warn_insecure_config, verify_token
from app.core.ratelimit import limiter
from app.api.router import api_router
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    warn_insecure_config()
    settings.enforce_production_security()
    await init_db()
    await recover_stale_sync()
    await recover_stale_mining()
    from app.core.recovery import rerun_pending_mining
    await rerun_pending_mining()
    await start_scheduler()
    yield
    await stop_scheduler()
    from app.core.executor import shutdown_executors
    shutdown_executors()


from app.core.config import settings
_app_kwargs = {
    "title": settings.app_name,
    "version": settings.app_version,
    "description": settings.app_description or None,
    "lifespan": lifespan,
}
if settings.app_env != "development":
    _app_kwargs["docs_url"] = None
    _app_kwargs["redoc_url"] = None
    _app_kwargs["openapi_url"] = None
app = FastAPI(**_app_kwargs)

# slowapi 限流
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

setup_middleware(app)
app.add_middleware(SlowAPIMiddleware)
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(Exception, general_error_handler)
app.include_router(api_router, prefix="/api/v1")
from app.api.config import router as config_router
from app.api.docs import router as docs_router
app.include_router(config_router)
app.include_router(docs_router)


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
        "version": settings.app_version,
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
from app.core.config import settings

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = Query(None)):
    """WebSocket 实时推送：同步进度、回测进度等。

    AUTH_ENABLED 时需通过 ?token=<jwt> 校验。
    """
    if settings.auth_enabled:
        if not token or verify_token(token) is None:
            await ws.close(code=4401)
            return
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
