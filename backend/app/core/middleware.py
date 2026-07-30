import time
import uuid
import logging
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware

from app.core.logging_config import request_id_var

perf_logger = logging.getLogger("perf")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
        request.state.request_id = request_id
        # 设置 contextvar，使所有后续日志自动携带 request_id
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
    from app.core.config import settings
    # CORS 来源可配：环境变量 CORS_ORIGINS（逗号分隔）或 config.api.cors_origins
    import os
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
    app.add_middleware(RequestContextMiddleware)
