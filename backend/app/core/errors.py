from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code = code
        self.message = message
        self.status = status


class DataFetchError(AppError):
    def __init__(self, message="数据获取失败"):
        super().__init__("DATA_FETCH_ERROR", message, 502)


class AIProviderUnavailableError(AppError):
    def __init__(self, message="AI 服务暂时不可用"):
        super().__init__("AI_PROVIDER_UNAVAILABLE", message, 503)


class AINotConfiguredError(AppError):
    def __init__(self, message="AI API Key 未配置"):
        super().__init__("AI_NOT_CONFIGURED", message, 503)


class ValidationError(AppError):
    def __init__(self, message="参数校验失败"):
        super().__init__("VALIDATION_ERROR", message, 422)


async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.status,
        content={"ok": False, "error": {"code": exc.code, "message": exc.message, "status": exc.status}},
    )


async def general_error_handler(request: Request, exc: Exception):
    import logging
    from app.core.logging_config import request_id_var
    rid = request_id_var.get("")
    logger = logging.getLogger(__name__)
    logger.exception("Unhandled exception [req=%s]: %s", rid, exc)
    return JSONResponse(
        status_code=500,
        content={"ok": False, "error": {"code": "INTERNAL_ERROR", "message": "内部服务器错误", "status": 500}},
    )
