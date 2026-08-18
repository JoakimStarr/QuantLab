"""认证 API：登录/注册/登出/状态探测。

自定义 JSON 端点，替换 fastapi-users 内置路由（内置 /auth/login 是 OAuth2 表单流，
与前端 axios JSON 约定及 ApiResponse 包装不兼容，且无法直接挂 slowapi 限流装饰器）。

安全设计：
- 登录限流：login_rate_limit（默认 5/minute，IP 维度）防暴力破解
- 注册限流：5/minute 防批量注册刷库
- 防枚举：登录失败统一返回"邮箱或密码错误"，不区分邮箱是否存在
- 审计打点：端点内直接调用 audit()（原 AuditAuthMiddleware 已移除，
  BaseHTTPMiddleware 中读 JSON body 存在流消费陷阱）

底层仍复用 fastapi-users 核心：UserManager（zxcvbn 密码强度）、JWTStrategy 签发。
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi_users.exceptions import (
    InvalidPasswordException,
    UserAlreadyExists,
    UserNotExists,
)

from app.core.audit_log import audit
from app.core.auth import (
    get_jwt_strategy,
    get_user_manager,
    require_user,
)
from app.core.config import settings
from app.core.ratelimit import limiter
from app.models.user import User
from app.schemas.common import ApiResponse
from app.schemas.user import LoginRequest, RegisterRequest, UserCreate

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "is_superuser": user.is_superuser,
    }


@router.post("/login")
@limiter.limit(settings.security.login_rate_limit)
async def login(request: Request, body: LoginRequest, user_manager=Depends(get_user_manager)):
    """邮箱 + 密码登录（JSON），签发 JWT。

    失败统一返回 401"邮箱或密码错误"（防账号枚举）；成功审计打点。
    """
    try:
        user = await user_manager.get_by_email(body.email)
    except UserNotExists:
        user = None
    if user is None or not user.is_active:
        audit("login_failed", user=body.email, resource="auth", detail="用户不存在或已禁用")
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    verified, _ = user_manager.password_helper.verify_and_update(
        body.password, user.hashed_password
    )
    if not verified:
        audit("login_failed", user=body.email, resource="auth", detail="密码错误")
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    token = await get_jwt_strategy().write_token(user)
    audit("login", user=body.email, resource="auth", detail="登录成功")
    return ApiResponse(ok=True, data={"token": token, "user": _user_payload(user)})


@router.post("/register")
@limiter.limit("5/minute")
async def register(request: Request, body: RegisterRequest, user_manager=Depends(get_user_manager)):
    """注册新账号（JSON），成功即登录（直接签发 token）。

    密码强度由 UserManager 的 zxcvbn 校验强制（score >= 2）；
    邮箱重复返回 409；限流防批量注册。
    """
    try:
        user = await user_manager.create(
            UserCreate(email=body.email, password=body.password, is_superuser=False),
            safe=True,
        )
    except UserAlreadyExists:
        raise HTTPException(status_code=409, detail="该邮箱已注册")
    except InvalidPasswordException as e:
        raise HTTPException(status_code=400, detail=str(e.reason))
    token = await get_jwt_strategy().write_token(user)
    audit("register", user=body.email, resource="auth", detail="注册成功")
    return ApiResponse(ok=True, data={"token": token, "user": _user_payload(user)})


@router.post("/logout")
async def logout(user=Depends(require_user)):
    """登出（JWT 无状态，前端删除本地 token；此处仅审计打点）。"""
    email = getattr(user, "email", "local-dev")
    audit("logout", user=email, resource="auth", detail="登出")
    return ApiResponse(ok=True)


# ---------------- 公开探测接口 ----------------


@router.get("/status")
async def auth_status():
    """前端探测鉴权是否开启（公开接口，用于决定是否跳转登录页）。

    设计上必须公开：前端在登录前调用它判断是否需要展示登录页。
    仅暴露 auth_enabled 布尔值，无其他敏感信息。
    """
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
async def ai_status(_: User = Depends(require_user)):
    """探测可用 AI Provider（需登录，Mining 页 badge 用）。

    收紧原因：provider/model 清单对攻击者有侦察价值；
    前端仅登录后的 Mining 页调用，附带 Bearer token，不受影响。
    """
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
