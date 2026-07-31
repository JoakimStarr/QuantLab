from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi import HTTPException


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


async def validation_error_handler(request: Request, exc: RequestValidationError):
    """统一 Pydantic 校验错误响应为 {ok, error} 结构。"""
    try:
        from app.core.logging_config import request_id_var
        rid = request_id_var.get("")
    except Exception:
        rid = ""
    import logging
    logging.getLogger(__name__).warning("校验失败 [req=%s]: %s", rid, exc.errors())
    return JSONResponse(
        status_code=422,
        content={"ok": False, "error": {"code": "VALIDATION_ERROR", "message": "参数校验失败", "status": 422, "details": exc.errors()}},
    )


async def http_exception_handler(request: Request, exc: HTTPException):
    """统一 HTTPException 响应为 {ok, error} 结构（含鉴权 401）。"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "error": {"code": "HTTP_ERROR", "message": str(exc.detail), "status": exc.status_code}},
    )
