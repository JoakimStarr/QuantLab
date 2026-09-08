from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    ok: bool
    data: T | None = None
    error: dict | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    status: int
    # 可操作建议（如"请先去数据同步中心同步数据"），前端可据此引导用户
    suggestion: str | None = None
