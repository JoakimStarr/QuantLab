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


class AuditAuthMiddleware(BaseHTTPMiddleware):
    """审计登录/登出事件（fastapi-users 内置路由不便在端点内打点）。

    仅对 POST /auth/login、/auth/logout 做审计：登录从表单取用户名，
    登出从 Bearer token 解析操作者；请求失败（status>=400）不记录。
    审计事件走统一日志管道（logger=audit → logs/quantlab.log）。
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        is_login = path.endswith("/auth/login")
        is_logout = path.endswith("/auth/logout")
        if not (is_login or is_logout):
            return await call_next(request)

        if is_login:
            # OAuth2 密码流：body 为 form 数据 {username, password}
            try:
                form = await request.form()
                username = str(form.get("username", ""))
            except Exception:  # noqa: BLE001
                username = ""
        else:
            username = ""
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                from app.core.auth import verify_token

                payload = verify_token(auth_header[7:])
                if payload and payload.get("sub"):
                    username = payload["sub"]

        response = await call_next(request)
        if response.status_code < 400:
            from app.core.audit_log import audit

            if is_login:
                audit("login", user=username, resource="auth", detail="登录成功")
            else:
                audit("logout", user=username, resource="auth", detail="登出")
        return response


def setup_cors(app):
    # CORS 来源可配：环境变量 CORS_ORIGINS（逗号分隔）或 config.api.cors_origins
    import os

    from app.core.config import settings
    cors_env = os.getenv("CORS_ORIGINS", "")
    if cors_env:
        origins = [o.strip() for o in cors_env.split(",") if o.strip()]
    else:
        origins = (settings.api or {}).get("cors_origins") or [
            "http://localhost:3000", "http://127.0.0.1:3000",
        ]
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
    app.add_middleware(AuditAuthMiddleware)
    # PerfLogMiddleware 在内部，依赖 correlation_id.get() 获取 request_id
    app.add_middleware(PerfLogMiddleware)
    # CorrelationIdMiddleware 在最外层（后添加的先执行），自动读取/生成 X-Request-ID
    app.add_middleware(CorrelationIdMiddleware)
