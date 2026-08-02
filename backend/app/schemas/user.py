"""用户 Pydantic schema（fastapi-users 注册/读取/更新用）。"""
from fastapi_users import schemas


class UserRead(schemas.BaseUser[int]):
    """用户读取模型。"""


class UserCreate(schemas.BaseUserCreate):
    """用户注册模型。"""


class UserUpdate(schemas.BaseUserUpdate):
    """用户更新模型。"""
