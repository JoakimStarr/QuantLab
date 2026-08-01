"""UserManager：fastapi-users 用户管理，集成 zxcvbn 密码强度校验。"""
import logging
from typing import Optional

import zxcvbn
from fastapi import Request
from fastapi_users import BaseUserManager, IntegerIDMixin, InvalidPasswordException
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase

from app.core.config import settings
from app.models.user import User

logger = logging.getLogger(__name__)


class UserManager(IntegerIDMixin, BaseUserManager[User, int]):
    """用户管理器：负责注册、密码校验、密码重置等。"""

    reset_password_token_secret = settings.secret_key
    verification_token_secret = settings.secret_key

    async def validate_password(self, password: str, user: User) -> None:
        """使用 zxcvbn 校验密码强度（score >= 2 为合格）。"""
        result = zxcvbn.zxcvbn(password)
        if result["score"] < 2:
            suggestions = result.get("feedback", {}).get("suggestions", [])
            reason = "密码强度不足"
            if suggestions:
                reason += "：" + "；".join(suggestions)
            raise InvalidPasswordException(reason=reason)

    async def on_after_register(self, user: User, request: Optional[Request] = None):
        """注册后回调。"""
        logger.info("新用户注册成功: id=%d email=%s", user.id, user.email)

    async def on_after_forgot_password(
        self, user: User, token: str, request: Optional[Request] = None
    ):
        """忘记密码后回调。"""
        logger.info("密码重置请求: id=%d email=%s", user.id, user.email)

    async def on_after_update(
        self, user: User, update_dict: dict, request: Optional[Request] = None
    ):
        """用户信息更新回调。"""
        logger.info("用户信息更新: id=%d email=%s", user.id, user.email)