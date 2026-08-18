"""用户 Pydantic schema（fastapi-users 注册/读取/更新用）。"""
from fastapi_users import schemas
from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    """登录请求体（JSON，对齐前端 axios 约定）。"""

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RegisterRequest(BaseModel):
    """注册请求体（JSON）。密码强度由后端 zxcvbn 校验（score >= 2）。"""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserRead(schemas.BaseUser[int]):
    """用户读取模型。"""


class UserCreate(schemas.BaseUserCreate):
    """用户注册模型。"""


class UserUpdate(schemas.BaseUserUpdate):
    """用户更新模型。"""
