"""JWT 认证模块：token 签发与校验。"""
import os
import time
import hmac
import hashlib
import json
import base64
from fastapi import Request, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer(auto_error=False)


def _get_secret() -> str:
    return os.getenv("SECRET_KEY", "change_this_to_random_string")


def create_token(data: dict, expire_seconds: int = 86400) -> str:
    """签发简单 JWT-like token（HS256）。"""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    exp = int(time.time()) + expire_seconds
    payload_data = {**data, "exp": exp, "iat": int(time.time())}
    payload = base64.urlsafe_b64encode(json.dumps(payload_data).encode()).rstrip(b"=").decode()
    msg = f"{header}.{payload}"
    sig = base64.urlsafe_b64encode(hmac.new(_get_secret().encode(), msg.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    return f"{msg}.{sig}"


def verify_token(token: str) -> dict | None:
    """校验 token 并返回 payload，失败返回 None。"""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, payload, sig = parts
        msg = f"{header}.{payload}"
        expected_sig = base64.urlsafe_b64encode(hmac.new(_get_secret().encode(), msg.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if data.get("exp", 0) < time.time():
            return None
        return data
    except Exception:
        return None


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = None,
):
    """认证依赖：从 Authorization header 提取 Bearer token 并校验。"""
    if credentials is None:
        # 也尝试从 cookie 读取
        token = request.cookies.get("auth_token")
        if token:
            payload = verify_token(token)
            if payload:
                return payload
        raise HTTPException(status_code=401, detail="未登录或 token 已过期")
    payload = verify_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="token 无效或已过期")
    return payload


async def optional_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = None,
):
    """可选认证依赖：不强制要求登录，但尝试解析 token。"""
    if credentials:
        return verify_token(credentials.credentials)
    token = request.cookies.get("auth_token")
    if token:
        return verify_token(token)
    return None