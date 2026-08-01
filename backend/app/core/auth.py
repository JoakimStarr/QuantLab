"""JWT 认证模块：token 签发与校验 + 可配置鉴权依赖。"""
import re
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


def create_refresh_token(data: dict) -> str:
    """签发 refresh token（7 天有效期）。"""
    return create_token(data, expire_seconds=604800)


def check_password_strength(password: str) -> tuple[bool, str]:
    """检查密码强度。

    要求：长度 >= 8，包含大写字母、小写字母、数字、特殊字符至少3种。
    """
    if len(password) < 8:
        return False, "密码长度至少 8 位"
    categories = 0
    if re.search(r'[A-Z]', password): categories += 1
    if re.search(r'[a-z]', password): categories += 1
    if re.search(r'[0-9]', password): categories += 1
    if re.search(r'[^A-Za-z0-9]', password): categories += 1
    if categories < 3:
        return False, "密码需包含大写字母、小写字母、数字、特殊字符中至少3种"
    return True, ""


def create_token(data: dict, expire_seconds: int | None = None) -> str:
    """签发简单 JWT-like token（HS256）。

    默认过期时间从 settings.security.access_token_expire_hours 读取。
    """
    if expire_seconds is None:
        expire_seconds = settings.security.access_token_expire_hours * 3600
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


def warn_insecure_config() -> None:
    """启动时输出安全配置告警。

    开发环境（APP_ENV=development）降为 DEBUG，避免无鉴权/默认口令告警刷屏
    （本地开发本就预期如此）；生产环境保持 WARNING 以提醒加固。
    """
    import os
    is_dev = os.getenv("APP_ENV", "development") == "development"
    level = logging.DEBUG if is_dev else logging.WARNING
    for w in settings.validate_security():
        logger.log(level, "[安全] %s", w)
