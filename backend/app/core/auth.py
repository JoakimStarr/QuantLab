"""认证模块：使用 fastapi-users 开源库实现 JWT 鉴权 + zxcvbn 密码强度校验。

保留与现有代码兼容的导出接口：
- require_user: 业务接口鉴权依赖（AUTH_ENABLED=False 时放行）
- current_user: 强制鉴权的 current_user 依赖
- verify_token: token 校验（WebSocket 兼容）
- warn_insecure_config: 启动时安全配置告警
- check_password_strength: zxcvbn 密码强度校验
"""
import logging
import os
from typing import Optional, Union

import zxcvbn
from fastapi import Depends, HTTPException, Request
from fastapi_users import FastAPIUsers
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.user import User

logger = logging.getLogger(__name__)

# ============================================================
# fastapi-users 认证后端配置
# ============================================================

bearer_transport = BearerTransport(tokenUrl="api/v1/auth/login")


def get_jwt_strategy() -> JWTStrategy:
    """JWT 策略工厂：使用 settings.secret_key 签名，有效期从配置读取。"""
    return JWTStrategy(
        secret=settings.secret_key,
        lifetime_seconds=settings.security.access_token_expire_hours * 3600,
    )


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)


# ============================================================
# User DB / User Manager 依赖
# ============================================================

async def get_user_db(db: AsyncSession = Depends(get_db)) -> SQLAlchemyUserDatabase:
    """提供 SQLAlchemyUserDatabase 依赖。"""
    yield SQLAlchemyUserDatabase(db, User)


# 循环导入规避：user_manager 依赖 auth（settings），所以延迟导入
from app.core.user_manager import UserManager  # noqa: E402


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> UserManager:
    """提供 UserManager 依赖。"""
    yield UserManager(user_db)


# ============================================================
# FastAPI Users 实例
# ============================================================

fastapi_users = FastAPIUsers[User, int](
    get_user_manager,
    [auth_backend],
)


# ============================================================
# 兼容接口：业务接口鉴权依赖
# ============================================================

async def require_user(
    request: Request,
    user: Optional[User] = Depends(fastapi_users.current_user(optional=True)),
) -> Union[User, dict]:
    """业务接口鉴权依赖。

    AUTH_ENABLED=False（本地开发）时直接放行，返回模拟用户信息；
    AUTH_ENABLED=True 时强制校验 Bearer token，返回 User 模型实例。
    """
    if not settings.auth_enabled:
        return {"role": "admin", "sub": "local-dev"}
    if user is None:
        raise HTTPException(status_code=401, detail="未登录或 token 已过期")
    return user


current_user = fastapi_users.current_user()


# ============================================================
# 兼容接口：Token 校验（WebSocket 无状态场景）
# ============================================================

def verify_token(token: str) -> Optional[dict]:
    """校验 JWT token 并返回 payload（WebSocket 等无状态场景使用）。

    使用 fastapi-users 内部的 JWT 解码逻辑（pyjwt），
    返回 dict 兼容旧接口格式，校验失败返回 None。
    """
    try:
        from fastapi_users.authentication.strategy.jwt import decode_jwt

        payload = decode_jwt(
            token,
            settings.secret_key,
            audience=["fastapi-users:auth"],
        )
        return {
            "sub": str(payload.get("sub")),
            "role": "admin",
            "exp": payload.get("exp"),
        }
    except Exception:
        return None


# ============================================================
# 兼容接口：密码强度校验
# ============================================================

def check_password_strength(password: str) -> tuple[bool, str]:
    """使用 zxcvbn 检查密码强度。

    Returns:
        (True, "") 如果密码强度合格（score >= 2）；
        (False, 原因) 如果密码强度不足。
    """
    result = zxcvbn.zxcvbn(password)
    if result["score"] < 2:
        suggestions = result.get("feedback", {}).get("suggestions", [])
        msg = "密码强度不足"
        if suggestions:
            msg += "：" + "；".join(suggestions)
        return False, msg
    return True, ""


# ============================================================
# 兼容接口：安全配置告警
# ============================================================

def warn_insecure_config() -> None:
    """启动时输出安全配置告警。

    开发环境（APP_ENV=development）降为 DEBUG 级别，
    生产环境保持 WARNING 级别。
    """
    is_dev = os.getenv("APP_ENV", "development") == "development"
    level = logging.DEBUG if is_dev else logging.WARNING
    for w in settings.validate_security():
        logger.log(level, "[安全] %s", w)