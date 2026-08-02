"""认证 API：注册/登录/登出/状态。使用 fastapi-users 路由。"""
from fastapi import APIRouter, Depends

from app.core.auth import (
    auth_backend,
    fastapi_users,
    require_user,
)
from app.core.config import settings
from app.models.user import User
from app.schemas.common import ApiResponse
from app.schemas.user import UserCreate, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])

# fastapi-users 内置路由：/login (POST), /logout (POST), /register (POST)
router.include_router(
    fastapi_users.get_auth_router(auth_backend),
    prefix="",
)
router.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="",
)

# 自定义公开接口


@router.get("/status")
async def auth_status():
    """前端探测鉴权是否开启（公开接口，用于决定是否跳转登录页）。"""
    return ApiResponse(ok=True, data={"auth_enabled": settings.security.auth_enabled})


@router.get("/me")
async def me(user: User = Depends(require_user)):
    """获取当前用户信息。"""
    if isinstance(user, dict):
        # AUTH_ENABLED=False 开发模式
        return ApiResponse(ok=True, data={"role": user.get("role", "admin"), "email": "local-dev"})
    return ApiResponse(
        ok=True,
        data={
            "id": user.id,
            "email": user.email,
            "is_active": user.is_active,
            "is_superuser": user.is_superuser,
            "is_verified": user.is_verified,
        },
    )


@router.get("/ai-status")
async def ai_status():
    """探测可用 AI Provider（公开接口，Mining 页 badge 用）。"""
    from app.services.ai.provider_router import ProviderRouter

    router_ = ProviderRouter()
    providers = []
    if router_.primary:
        providers.append({"provider": "opencodezen", "model": router_.primary.model, "ready": True})
    if router_.fallback:
        providers.append({"provider": "glm", "model": router_.fallback.model, "ready": True})
    if router_.tertiary:
        providers.append({"provider": "siliconflow", "model": router_.tertiary.model, "ready": True})
    return ApiResponse(ok=True, data={"providers": providers, "count": len(providers)})
