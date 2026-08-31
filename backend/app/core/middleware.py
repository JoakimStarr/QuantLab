import logging
import time

from asgi_correlation_id import correlation_id
from asgi_correlation_id.middleware import CorrelationIdMiddleware
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware

from app.core.logging_config import request_id_var

perf_logger = logging.getLogger("perf")


class PerfLogMiddleware(BaseHTTPMiddleware):
    """性能日志中间件（维持原有的 perf log 功能）"""

    async def dispatch(self, request: Request, call_next):
        request_id = correlation_id.get() or request.headers.get("X-Request-ID", "")
        token = request_id_var.set(request_id)
        start_time = time.time()
        try:
            response = await call_next(request)
            elapsed = time.time() - start_time
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Response-Time"] = f"{elapsed * 1000:.0f}ms"
            perf_logger.info(
                "api_perf", extra={"extra_fields": {
                    "request_id": request_id, "method": request.method,
                    "path": request.url.path, "status": response.status_code,
                    "elapsed_ms": round(elapsed * 1000, 1),
                }}
            )
            return response
        except Exception:
            elapsed = time.time() - start_time
            perf_logger.error(
                "api_error", extra={"extra_fields": {
                    "request_id": request_id, "method": request.method,
                    "path": request.url.path, "status": 500,
                    "elapsed_ms": round(elapsed * 1000, 1),
                }}, exc_info=True
            )
            raise
        finally:
            request_id_var.reset(token)


def setup_cors(app):
    # CORS 来源可配：环境变量 CORS_ORIGINS（逗号分隔）或 config.api.cors_origins
    import os

    from app.core.config import settings
    cors_env = os.getenv("CORS_ORIGINS", "")
    if cors_env:
        origins = [o.strip() for o in cors_env.split(",") if o.strip()]
    else:
        fp = os.getenv("FRONTEND_PORT", "3001")
        origins = (settings.api or {}).get("cors_origins") or [
            f"http://localhost:{fp}", f"http://127.0.0.1:{fp}",
        ]
    # 安全断言：'*' + allow_credentials=True 时 starlette 会回显任意 Origin，
    # 等于向所有网站开放带凭证跨域——直接拒绝启动（fail fast）
    if "*" in origins:
        raise ValueError(
            "CORS_ORIGINS 含 '*' 且 allow_credentials=True 为危险组合，"
            "请显式列出允许的来源（逗号分隔），如 http://localhost:3001"
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def setup_middleware(app):
    setup_cors(app)
    # 审计中间件（登录/登出打点）在内部，依赖 auth 路由挂载
    # PerfLogMiddleware 在内部，依赖 correlation_id.get() 获取 request_id
    app.add_middleware(PerfLogMiddleware)
    # CorrelationIdMiddleware 在最外层（后添加的先执行），自动读取/生成 X-Request-ID
    app.add_middleware(CorrelationIdMiddleware)
