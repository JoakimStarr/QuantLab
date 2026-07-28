from typing import Generic, TypeVar, Optional
from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    ok: bool
    data: Optional[T] = None
    error: Optional[dict] = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    status: int