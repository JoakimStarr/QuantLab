"""认证与鉴权集成测试。"""

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    """构造 TestClient。DB 不可达时 skip（lifespan 会触发 init_db）。"""
    if not os.getenv("DATABASE_URL", "").strip():
        pytest.skip("需要 Postgres（DATABASE_URL 未设置）")
    from app.main import app

    try:
        with TestClient(app) as c:
            yield c
    except Exception as e:
        pytest.skip(f"DB 不可达或 lifespan 失败: {e}")


def test_auth_status_public(client):
    """/auth/status 为公开接口，返回 auth_enabled。"""
    r = client.get("/api/v1/auth/status")
    assert r.status_code == 200
    assert "auth_enabled" in r.json()["data"]


def test_login_wrong_password(client):
    r = client.post("/api/v1/auth/login", json={"password": "definitely-wrong-pwd"})
    assert r.json()["ok"] is False


def test_login_correct_and_me(client):
    """正确口令登录拿到 token，/me 可用。"""
    from app.core.config import settings

    r = client.post("/api/v1/auth/login", json={"password": settings.security.admin_password})
    assert r.json()["ok"] is True
    token = r.json()["data"]["token"]
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["data"]["role"] == "admin"


def test_business_endpoint_protected_when_auth_enabled(client, monkeypatch):
    """开启鉴权后：无 token 业务接口 401，带 token 200。"""
    from app.core.config import settings

    monkeypatch.setattr(settings.security, "auth_enabled", True)

    r = client.get("/api/v1/factors")
    assert r.status_code == 401

    r = client.post("/api/v1/auth/login", json={"password": settings.security.admin_password})
    token = r.json()["data"]["token"]
    r = client.get("/api/v1/factors", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_forged_token_rejected(client, monkeypatch):
    """伪造/篡改 token 应被拒绝。"""
    from app.core.config import settings

    monkeypatch.setattr(settings.security, "auth_enabled", True)
    r = client.get("/api/v1/factors", headers={"Authorization": "Bearer fake.token.here"})
    assert r.status_code == 401


def test_default_secret_key_flagged(monkeypatch):
    """默认 SECRET_KEY 应被启动校验标记。"""
    from app.core.config import settings

    monkeypatch.setattr(settings.security, "secret_key", "change_this_to_random_string")
    warnings = settings.validate_security()
    assert any("SECRET_KEY" in w for w in warnings)


def test_negative_ref_rejected():
    """负数 Ref（未来数据）必须被沙箱拒绝——look-ahead 安全回归。"""
    from app.services.factor.expression import ExpressionValidationError, validate_expression

    with pytest.raises(ExpressionValidationError):
        validate_expression("Ref(Mean($close,5), -1) / $close - 1")
