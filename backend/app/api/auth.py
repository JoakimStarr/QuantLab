"""认证 API：登录/登出/状态。"""
from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel
from app.core.auth import create_token, require_user, verify_admin_password
from app.core.config import settings
from app.core.ratelimit import limiter
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str


@router.get("/status")
async def auth_status():
    """前端探测鉴权是否开启（公开接口，用于决定是否跳转登录页）。"""
    return ApiResponse(ok=True, data={"auth_enabled": settings.auth_enabled})


@router.post("/login")
@limiter.limit(settings.login_rate_limit)
async def login(request: Request, req: LoginRequest):
    """简单密码登录，返回 JWT token。口令用 bcrypt 校验，登录限流防暴力破解。"""
    if not verify_admin_password(req.password):
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=401,
            content={"ok": False, "error": {"code": "AUTH_FAILED", "message": "密码错误", "status": 401}},
        )
    token = create_token({"role": "admin"}, expire_seconds=86400 * 7)  # 7天
    return ApiResponse(ok=True, data={"token": token, "token_type": "bearer"})


@router.get("/me")
async def me(user: dict = Depends(require_user)):
    """获取当前用户信息。"""
    return ApiResponse(ok=True, data={"role": user.get("role", "admin"), "exp": user.get("exp")})


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
