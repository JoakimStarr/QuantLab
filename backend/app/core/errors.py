from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, suggestion: str = None):
        self.code = code
        self.message = message
        self.status = status
        # 可操作提示：随 error payload 下发，前端可展示"怎么修"（如去同步）
        self.suggestion = suggestion


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
    # QLIB_NOT_AVAILABLE 的兜底操作建议：指明去数据管理/同步中心，前端可弹"去同步"
    suggestion = exc.suggestion
    if suggestion is None and exc.code == "QLIB_NOT_AVAILABLE":
        suggestion = "行情数据尚未同步或 qlib 不可用，请先在数据管理页/同步中心发起一键全同步或增量同步后再试"
    error = {"code": exc.code, "message": exc.message, "status": exc.status}
    if suggestion:
        error["suggestion"] = suggestion
    return JSONResponse(
        status_code=exc.status,
        content={"ok": False, "error": error},
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
        content={"ok": False, "error": {"code": "VALIDATION_ERROR",
                                        "message": "参数校验失败", "status": 422, "details": exc.errors()}},
    )


async def http_exception_handler(request: Request, exc: HTTPException):
    """统一 HTTPException 响应为 {ok, error} 结构（含鉴权 401）。"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "error": {"code": "HTTP_ERROR", "message": str(exc.detail), "status": exc.status_code}},
    )
