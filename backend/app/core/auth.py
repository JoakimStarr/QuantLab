"""JWT 认证模块：token 签发与校验 + 可配置鉴权依赖。"""
import time
import hmac
import hashlib
import json
import base64
import logging
import bcrypt
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.config import settings

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)

# 明文口令首次校验时哈希后缓存，避免每次重新哈希
_admin_hash_cache: bytes | None = None


def _get_secret() -> str:
    return settings.secret_key


def _b64decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _get_admin_hash() -> bytes:
    """获取管理员口令的 bcrypt 哈希字节。

    优先使用 ADMIN_PASSWORD_HASH（推荐）；否则将 ADMIN_PASSWORD 明文哈希一次后缓存。
    """
    global _admin_hash_cache
    if settings.admin_password_hash:
        return settings.admin_password_hash.encode()
    if _admin_hash_cache is None:
        _admin_hash_cache = bcrypt.hashpw(settings.admin_password.encode(), bcrypt.gensalt())
    return _admin_hash_cache


def verify_admin_password(password: str) -> bool:
    """校验管理员口令（bcrypt 恒定时间比较）。"""
    try:
        return bcrypt.checkpw(password.encode(), _get_admin_hash())
    except Exception:
        return False


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
    """校验 token 并返回 payload，失败返回 None。

    安全要点：
    - 强制 header.alg == HS256，拒绝 alg:none 等绕过
    - hmac.compare_digest 恒定时间比较签名
    - 校验 exp 过期
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, payload, sig = parts
        # 校验算法，防止 alg:none 绕过
        header_data = json.loads(_b64decode(header))
        if header_data.get("alg") != "HS256":
            return None
        msg = f"{header}.{payload}"
        expected_sig = base64.urlsafe_b64encode(hmac.new(_get_secret().encode(), msg.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        data = json.loads(_b64decode(payload))
        if data.get("exp", 0) < time.time():
            return None
        return data
    except Exception:
        return None


def _extract_payload(request: Request, credentials: HTTPAuthorizationCredentials | None) -> dict | None:
    """从 Bearer header 或 cookie 提取并校验 token。"""
    if credentials:
        return verify_token(credentials.credentials)
    token = request.cookies.get("auth_token")
    if token:
        return verify_token(token)
    return None


async def require_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    """业务接口鉴权依赖。

    AUTH_ENABLED=False（本地开发）时直接放行；否则强制校验 token。
    """
    if not settings.auth_enabled:
        return {"role": "admin", "sub": "local-dev"}
    payload = _extract_payload(request, credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="未登录或 token 已过期")
    return payload


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    """认证依赖：从 Authorization header 或 cookie 提取 token 并校验。"""
    payload = _extract_payload(request, credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="token 无效或已过期")
    return payload


async def optional_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    """可选认证依赖：不强制要求登录，但尝试解析 token。"""
    return _extract_payload(request, credentials)


def warn_insecure_config() -> None:
    """启动时输出安全配置告警。"""
    for w in settings.validate_security():
        logger.warning("[安全] %s", w)
