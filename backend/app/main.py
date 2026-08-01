import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.config import router as config_router
from app.api.docs import router as docs_router
from app.api.router import api_router
from app.core.auth import verify_token, warn_insecure_config
from app.core.config import settings
from app.core.database import init_db
from app.core.errors import (
    AppError,
    app_error_handler,
    general_error_handler,
    http_exception_handler,
    validation_error_handler,
)
from app.core.logging_config import setup_logging, set_log_level
from app.core.metrics import router as metrics_router, ws_active_connections
from app.core.middleware import setup_middleware
from app.core.ratelimit import limiter
from app.core.recovery import recover_stale_mining, recover_stale_sync
from app.core.scheduler import start_scheduler, stop_scheduler
from app.core.websocket_manager import ws_manager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(log_dir=settings.logging.dir, level=settings.logging.level)
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


@app.middleware("http")
async def add_request_id_middleware(request: Request, call_next):
    """为每个请求生成/提取 request_id，注入日志。"""
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
    # 设置到 ContextVar
    from app.core.logging_config import request_id_var

    request_id_var.set(request_id)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(Exception, general_error_handler)

# Starlette 层级的 404/405 默认会绕过 FastAPI handler，统一响应结构


async def _starlette_not_found_handler(request: Request, exc: StarletteHTTPException):
    """Starlette 抛 404 时（路由未匹配），统一响应结构。"""
    return JSONResponse(
        status_code=404,
        content={
            "ok": False,
            "error": {
                "code": "NOT_FOUND",
                "message": f"路径不存在: {request.url.path}",
                "status": 404,
            },
        },
    )


async def _starlette_method_not_allowed_handler(request: Request, exc: StarletteHTTPException):
    """Starlette 抛 405 时（方法不允许），统一响应结构。"""
    return JSONResponse(
        status_code=405,
        content={
            "ok": False,
            "error": {
                "code": "METHOD_NOT_ALLOWED",
                "message": f"{request.method} 不被允许: {request.url.path}",
                "status": 405,
            },
        },
    )


app.add_exception_handler(404, _starlette_not_found_handler)
app.add_exception_handler(405, _starlette_method_not_allowed_handler)

# Prometheus /metrics 端点（无需鉴权，供 Prometheus 抓取）
app.include_router(metrics_router)

# 业务 API 路由
app.include_router(api_router, prefix="/api/v1")
app.include_router(config_router, prefix="/api/v1")
app.include_router(docs_router, prefix="/api/v1")


@app.get("/health")
async def health():
    return await _build_health_payload()


@app.get("/api/v1/health")
async def health_v1():
    """健康检查别名：与 /health 同源，挂在 /api/v1 前缀下以兼容前端 api 实例调用。

    前端 api 实例 baseURL=/api/v1，调 api.get('/health') 会请求 /api/v1/health；
    之前该路径不存在，被 SPA fallback 捕获并重定向到 /docs（307），导致 AdminMetrics
    无法正确显示健康状态。现统一两个路径走同一份逻辑。
    """
    return await _build_health_payload()


async def _build_health_payload() -> dict:
    """统一健康检查：DB/qlib/调度器/磁盘/WS/AI。"""
    status = "ok"
    checks = {}
    # 数据库检查
    try:
        from app.core.database import health_check as db_health

        result = await db_health()
        if result["status"] == "ok":
            checks["database"] = "ok"
        else:
            checks["database"] = f"error: {result.get('error', 'unknown')}"
            status = "degraded"
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
    # 调度器检查
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        from app.core.scheduler import scheduler

        if isinstance(scheduler, AsyncIOScheduler) and scheduler.running:
            checks["scheduler"] = "running"
        else:
            checks["scheduler"] = "stopped"
            if status != "degraded":
                status = "degraded"
    except Exception:
        checks["scheduler"] = "unknown"
    # 磁盘空间检查（data 目录）
    try:
        import shutil

        data_path = settings.PROJECT_ROOT / "data"
        if data_path.exists():
            usage = shutil.disk_usage(str(data_path))
            free_gb = usage.free / (1024**3)
            checks["disk"] = f"{free_gb:.1f}GB free"
            if free_gb < 1.0:
                checks["disk"] += " (LOW)"
                status = "degraded"
        else:
            checks["disk"] = "data dir not found"
    except Exception:
        checks["disk"] = "unknown"
    # WebSocket 连接数
    try:
        checks["ws_connections"] = len(ws_manager._connections)
    except Exception:
        checks["ws_connections"] = "unknown"
    # AI Provider 检查（不发起真实请求，仅验证 key 是否已配置）
    try:
        from app.core.config import is_placeholder_api_key

        providers = []
        for name, key in (
            ("opencodezen", settings.opencodezen_api_key),
            ("glm", settings.glm_api_key),
            ("siliconflow", settings.siliconflow_api_key),
        ):
            if key and not is_placeholder_api_key(key):
                providers.append(name)
        checks["ai_providers"] = ",".join(providers) if providers else "none"
        if not providers:
            checks["ai_providers"] += " (check .env API keys)"
            status = "degraded"
    except Exception:
        checks["ai_providers"] = "unknown"
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


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = Query(None)):
    """WebSocket 实时推送：同步进度、回测进度等。

    AUTH_ENABLED 时需通过 ?token=<jwt> 校验。
    客户端应周期性发送 "ping" 文本帧以维持心跳；超时未 ping
    的连接会被后台 reaper 主动 close（close code 4408）。
    """
    if settings.auth_enabled:
        if not token or verify_token(token) is None:
            await ws.close(code=4401, reason="unauthorized")
            return
    await ws_manager.connect(ws)
    try:
        while True:
            # 保持连接，接收心跳
            data = await ws.receive_text()
            if data == "ping":
                ws_manager.update_heartbeat(ws)
                await ws_manager.send_to(ws, "pong", {"timestamp": datetime.now().isoformat()})
            else:
                # 任意客户端消息也算心跳（前端可能在调试时发其它消息）
                ws_manager.update_heartbeat(ws)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("WebSocket 异常断开", exc_info=True)
    finally:
        await ws_manager.disconnect(ws)
