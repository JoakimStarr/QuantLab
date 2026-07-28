"""认证 API：登录/登出。"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.core.auth import create_token, get_current_user
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str


@router.post("/login")
async def login(req: LoginRequest):
    """简单密码登录，返回 JWT token。"""
    import os
    admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
    if req.password != admin_password:
        return ApiResponse(ok=False, error={"code": "AUTH_FAILED", "message": "密码错误", "status": 401})
    token = create_token({"role": "admin"}, expire_seconds=86400 * 7)  # 7天
    return ApiResponse(ok=True, data={"token": token, "token_type": "bearer"})


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    """获取当前用户信息。"""
    return ApiResponse(ok=True, data={"role": user.get("role", "admin"), "exp": user.get("exp")})