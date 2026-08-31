"""系统设置 API：读取/保存 config.yaml + .env、AI Provider 状态与连通性测试。

保存策略：
- config.yaml 用 ruamel.yaml 往返读写，保留注释与键顺序；
- API Key 写入根目录 .env（KEY=VALUE 行 upsert）；
- 保存后调用 settings.reload() 热重载，并重置 ProviderRouter 单例，
  使新的 AI 配置在下次请求立即生效（无需重启后端）。
"""
import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.config import is_placeholder_api_key, settings
from app.schemas.common import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])

# 可编辑的 config.yaml 顶层分区
_EDITABLE_SECTIONS = ("ai_provider", "quant", "logging", "scheduler", "task", "monte_carlo", "app", "data")

# .env 中可写入的 API Key 字段（KEY 名 → settings 字段名）
_ENV_KEYS = {
    "GLM_API_KEY": "glm_api_key",
    "SILICONFLOW_API_KEY": "siliconflow_api_key",
    "OPENCODEZEN_API_KEY": "opencodezen_api_key",
    "FRED_API_KEY": "fred_api_key",
    "EIA_API_KEY": "eia_api_key",
}

# provider 逻辑名 -> .env KEY 名
_PROVIDER_ENV = {
    "opencodezen": "OPENCODEZEN_API_KEY",
    "glm": "GLM_API_KEY",
    "siliconflow": "SILICONFLOW_API_KEY",
}
# 各 slot 默认 provider（config 中 provider 字段缺失时兜底）
_SLOT_DEFAULT_PROVIDER = {
    "primary": "opencodezen",
    "fallback": "glm",
    "tertiary": "siliconflow",
}

_DEFAULT_ENV_VALUES = {
    "GLM_API_KEY": "your_glm_api_key_here",
    "SILICONFLOW_API_KEY": "your_siliconflow_api_key_here",
    "OPENCODEZEN_API_KEY": "your_opencodezen_api_key_here",
    "FRED_API_KEY": "your_fred_api_key_here",
    "EIA_API_KEY": "your_eia_api_key_here",
}

# 排除过大/二进制类不可编辑字段
_NON_EDITABLE_KEYS = {"sync_indices", "default_backtest_period"}


def _project_root() -> Path:
    return Path(os.getenv("PROJECT_ROOT") or settings.PROJECT_ROOT)


def _config_path() -> Path:
    return _project_root() / "config.yaml"


def _env_path() -> Path:
    return _project_root() / ".env"


def _mask_key(key: str) -> str:
    """API Key 掩码：仅保留首尾 4 位，中间用 * 填充。"""
    if not key:
        return ""
    if len(key) <= 10:
        return key[:2] + "***" + key[-2:]
    return key[:4] + "****" + key[-4:]


def _provider_ready(name: str, key: str, endpoint: dict) -> bool:
    return bool(key and not is_placeholder_api_key(key) and endpoint.get("model"))


def _status_snapshot() -> dict:
    """当前 AI Provider 状态快照（读自 ai_settings store，不含明文 key）。"""
    from app.services.ai import ai_settings_store as store

    ai_cfg = settings.ai_provider.model_dump() if hasattr(settings.ai_provider, "model_dump") else {}
    active_id = store.get_active_provider_id() or ""
    providers = []
    for p in store.get_providers():
        key = p.get("api_key") or ""
        providers.append({
            "key": p["id"],
            "provider": p.get("name", ""),
            "role": "main" if p["id"] == active_id else "fallback",
            "is_main": p["id"] == active_id,
            "configured": bool(key),
            "has_key": bool(key),
            "key_masked": store.mask_key(key) if key else "",
            "base_url": p.get("base_url", ""),
            "model": p.get("model", ""),
            "max_tokens": ai_cfg.get("max_tokens", 1024),
            "temperature": ai_cfg.get("temperature", 0.4),
            "timeout_seconds": ai_cfg.get("timeout_seconds", 30),
            "ready": bool(key and p.get("model")),
        })
    return {
        "providers": providers,
        "main_provider": active_id,
        "force_json_output": ai_cfg.get("force_json_output", True),
        "retry_times": ai_cfg.get("retry_times", 1),
        "route_budget_seconds": ai_cfg.get("route_budget_seconds", 120),
        "cache_ttl": ai_cfg.get("cache_ttl", "day"),
        "total_timeout_seconds": ai_cfg.get("total_timeout_seconds", 10),
    }


def _editable_snapshot() -> dict:
    """返回当前可编辑配置（去除密钥等敏感信息）。"""
    return {
        "ai_provider": {
            key: value for key, value in settings.ai_provider.model_dump().items()
            if key not in _NON_EDITABLE_KEYS
        },
        "quant": {
            key: value for key, value in settings.quant.model_dump().items()
            if key not in _NON_EDITABLE_KEYS
        },
        "logging": settings.logging.model_dump(),
        "scheduler": settings.scheduler.model_dump(),
        "task": settings.task.model_dump(),
        "monte_carlo": settings.monte_carlo.model_dump(),
        "app": settings.app.model_dump(),
        "data": settings.data.model_dump(),
    }


@router.get("")
async def get_settings():
    """获取可编辑的系统配置 + AI Provider 状态。"""
    data = _editable_snapshot()
    data["ai_provider_status"] = _status_snapshot()
    data["api_keys"] = {
        name.lower(): {
            "configured": bool(key and not is_placeholder_api_key(key)),
            "masked": _mask_key(key) if key else "",
        }
        for name, key in (
            ("OPENCODEZEN", settings.opencodezen_api_key),
            ("GLM", settings.glm_api_key),
            ("SILICONFLOW", settings.siliconflow_api_key),
            ("FRED", settings.fred_api_key),
            ("EIA", settings.eia_api_key),
        )
    }
    # 安全信息（只读展示）
    data["security_env"] = settings.security.app_env
    data["auth_enabled"] = settings.security.auth_enabled
    return ApiResponse(ok=True, data=data)


@router.get("/ai-providers")
async def get_ai_providers():
    """AI Provider 状态（配置/就绪/当前模型）。"""
    return ApiResponse(ok=True, data=_status_snapshot())


class ProviderTestRequest(BaseModel):
    """AI Provider 连通性测试请求。

    provider: 逻辑名（opencodezen/glm/siliconflow），或自定义 provider 名。
    api_key / config 可缺省：缺省时使用当前已保存配置；传入则临时覆盖测试。
    """

    provider: str = Field(..., description="provider 逻辑名")
    api_key: str = Field("", description="临时 API Key（缺省用已保存值）")
    config: dict = Field(default_factory=dict, description="临时覆盖 base_url/model/max_tokens/temperature/timeout_seconds")


@router.post("/ai-providers/test")
async def test_ai_provider(payload: ProviderTestRequest):
    """测试 AI Provider 连通性：发起一次极简对话，返回时延/模型/用量。"""
    from app.services.ai.llm_client import LLMClient

    name = payload.provider.strip().lower()
    mc = settings.ai_provider; ai_cfg = mc.model_dump() if hasattr(mc, "model_dump") else mc
    saved_cfg = {}
    for c in list(ai_cfg.get("providers") or []):
        c = c.model_dump() if hasattr(c, "model_dump") else c
        if (c.get("provider") or "").strip().lower() == name:
            saved_cfg = c
            break
    cfg = {**saved_cfg, **payload.config}
    api_key = (payload.api_key.strip()
               or getattr(settings, _ENV_KEYS.get(name.upper(), f"{name}_api_key"), "")
               or os.getenv(f"{name.upper()}_API_KEY", "") or "")

    if not api_key or is_placeholder_api_key(api_key):
        return ApiResponse(ok=False, error={
            "code": "NO_API_KEY",
            "message": f"Provider [{name}] 未配置 API Key",
            "status": 400,
        })

    try:
        client = LLMClient(
            api_key=api_key,
            base_url=cfg.get("base_url") or "",
            model=cfg.get("model") or "",
            timeout=int(cfg.get("timeout_seconds", 15)),
            max_tokens=int(cfg.get("max_tokens", 256)),
            temperature=float(cfg.get("temperature", 0.3)),
        )
    except Exception as e:
        return ApiResponse(ok=False, error={
            "code": "INIT_FAILED", "message": f"客户端初始化失败: {e}", "status": 400,
        })

    started = time.monotonic()
    try:
        result = await client.chat_completion(
            [{"role": "user", "content": "仅回复 OK"}],
            force_json=False,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        content = result.get("content", "")
        if isinstance(content, str):
            content = content.strip()[:200]
        return ApiResponse(ok=True, data={
            "provider": name,
            "model": result.get("model", cfg.get("model", "")),
            "latency_ms": latency_ms,
            "tokens": result.get("tokens", 0),
            "reply": content,
            "success": True,
        })
    except Exception as e:
        latency_ms = int((time.monotonic() - started) * 1000)
        return ApiResponse(ok=False, error={
            "code": "CONNECT_FAILED",
            "message": f"连接失败（{latency_ms}ms）: {e}",
            "status": 400,
        })


class SaveSettingsRequest(BaseModel):
    """保存设置请求：各分区可选，缺失分区保持现状。"""

    ai_provider: dict | None = None
    quant: dict | None = None
    logging: dict | None = None
    scheduler: dict | None = None
    task: dict | None = None
    monte_carlo: dict | None = None
    app: dict | None = None
    data: dict | None = None
    api_keys: dict[str, str] | None = None


def _upsert_env_file(path: Path, updates: dict[str, str]) -> None:
    """在 .env 文件中按 KEY=value 逐行 upsert，缺失行追加到末尾。"""
    lines: list[str] = []
    if path.exists():
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()

    existing_keys = set()
    updated: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            existing_keys.add(key)
            if key in updates:
                updated.append(f"{key}={updates[key]}")
                continue
        updated.append(line)

    for key, value in updates.items():
        if key not in existing_keys:
            updated.append(f"{key}={value}")

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(updated) + "\n")


def _save_config_yaml(updates: dict[str, dict]) -> None:
    """将分区更新合并进 config.yaml，保留注释（ruamel round-trip）。"""
    from ruamel.yaml import YAML

    path = _config_path()
    yml = YAML()
    yml.preserve_quotes = True
    if path.exists():
        with open(path, encoding="utf-8") as f:
            cfg = yml.load(f) or {}
    else:
        cfg = {}

    for section, values in updates.items():
        if not values:
            continue
        current = cfg.get(section)
        if isinstance(current, dict):
            if section == "ai_provider" and "providers" in values:
                # 新结构落盘时清除旧三槽，避免下次加载迁移出重复 provider
                for k in ("primary", "fallback", "tertiary"):
                    current.pop(k, None)
            current.update(values)
        else:
            cfg[section] = values

    with open(path, "w", encoding="utf-8") as f:
        yml.dump(cfg, f)


@router.put("")
async def save_settings(payload: SaveSettingsRequest):
    """保存系统设置：写入 config.yaml（保留注释）与 .env（API Keys），随后热重载。"""
    try:
        yaml_updates = {
            section: getattr(payload, section)
            for section in _EDITABLE_SECTIONS
            if getattr(payload, section) is not None
        }
        if yaml_updates:
            _save_config_yaml(yaml_updates)

        if payload.api_keys:
            env_updates = {}
            for key, value in payload.api_keys.items():
                env_name = key.strip().upper()
                stem = env_name[:-8] if env_name.endswith("_API_KEY") else ""
                if not (env_name.endswith("_API_KEY") and stem and stem.replace("_", "").isalnum()):
                    continue
                env_updates[env_name] = (value or "").strip()
            if env_updates:
                _upsert_env_file(_env_path(), env_updates)

        settings.reload()
        # 重置 AI 路由单例，使新配置立即生效
        from app.services.ai.provider_router import ProviderRouter

        ProviderRouter.reset()

        logger.info("设置已保存并热重载: yaml_sections=%s env_keys=%s",
                    sorted(yaml_updates.keys()), sorted((payload.api_keys or {}).keys()))
        return ApiResponse(ok=True, data={
            "message": "设置已保存，配置已热重载",
            "saved_sections": sorted(yaml_updates.keys()),
            "saved_api_keys": sorted((payload.api_keys or {}).keys()),
            "ai_provider_status": _status_snapshot(),
        })
    except Exception as e:
        logger.exception("保存设置失败")
        return ApiResponse(ok=False, error={
            "code": "SAVE_FAILED", "message": f"保存失败: {e}", "status": 500,
        })


# ============================================================
# 从 Quantlerning 移植：AI Provider 管理（JSON store）+ 获取模型 + 测试
# ============================================================

from app.services.ai import ai_settings_store as ai_store  # noqa: E402


class ProviderPayload(BaseModel):
    """provider 条目字段。api_key: None=保留原值、""=清除、非空=更新。"""
    name: str = Field("", max_length=100)
    base_url: str = Field("", max_length=300)
    model: str = Field("", max_length=200)
    api_key: str | None = Field(None, max_length=500)


class GlobalSettingsPayload(BaseModel):
    """全局参数：生成参数 + 联网搜索（provider 走独立端点）。"""
    max_tokens: int | None = Field(None, ge=16, le=8192)
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    web_search_key: str | None = Field(None, max_length=500)


class AITestPayload(BaseModel):
    """按表单提交的配置拉模型 / 测连接（保存前预览；未填项回退已保存/默认）。"""
    base_url: str = Field("", max_length=300)
    api_key: str = Field("", max_length=500)
    model: str = Field("", max_length=200)
    max_tokens: int | None = Field(None, ge=16, le=8192)
    temperature: float | None = Field(None, ge=0.0, le=2.0)


def _ai_err(code: str, message: str, status: int) -> ApiResponse:
    return ApiResponse(ok=False, error={"code": code, "message": message, "status": status})


async def _ai_fetch_models(base_url: str, api_key: str) -> list[str]:
    """调用 OpenAI 兼容 {base_url}/models 拉取模型 id 列表。"""
    import httpx

    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(trust_env=False, timeout=15) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
    return [m.get("id") for m in data.get("data") or [] if m.get("id")]


async def _ai_test_connection(cfg: dict) -> dict:
    """用完整请求配置做一次非流式调用，返回 {"ok","message","reply"?}。"""
    import httpx

    if not cfg.get("api_key"):
        return {"ok": False, "message": "未配置 API key：请先填写"}
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    body = {
        "model": cfg.get("model") or "",
        "messages": [{"role": "user", "content": "你好，请只回复两个字：正常"}],
        "max_tokens": int(cfg.get("max_tokens") or 64),
        "stream": False,
        "temperature": float(cfg.get("temperature") if cfg.get("temperature") is not None else 0.2),
    }
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=30) as client:
            resp = await client.post(url, headers=headers, json=body)
            if resp.status_code != 200:
                return {"ok": False, "message": f"HTTP {resp.status_code}: {resp.text[:200]}"}
            data = resp.json()
        reply = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return {"ok": True, "message": "连接成功", "reply": reply}
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"AI 服务连接失败: {exc.__class__.__name__}: {exc}"}
    except Exception:
        return {"ok": False, "message": "响应不是合法 JSON（可能 base_url 不对）"}


@router.get("/ai")
async def get_ai_settings():
    """返回完成 AI 配置：provider 列表（打码）+ active + 全局参数。"""
    return ApiResponse(ok=True, data=ai_store.public_config())


@router.put("/ai")
async def update_ai_settings(payload: GlobalSettingsPayload):
    """保存全局参数（生成参数 + 联网搜索）。"""
    ai_store.save_global_config(payload.model_dump())
    _ai_reset_router()
    return ApiResponse(ok=True, data=ai_store.public_config())


@router.get("/ai/models")
async def list_ai_models():
    """当前已配置模型列表（各 provider 的 model 去重），current 为 active 的模型。"""
    configured: list[str] = []
    for p in ai_store.get_providers():
        m = p.get("model")
        if m and m not in configured:
            configured.append(m)
    active = ai_store.get_active_provider()
    return ApiResponse(ok=True, data={
        "models": configured,
        "current": (active or {}).get("model", ""),
    })


@router.post("/ai/models")
async def fetch_ai_models(payload: AITestPayload):
    """按表单 base_url/api_key 拉取模型列表（保存前预览；失败返回 models=[] + error）。"""
    base = (payload.base_url or "").strip()
    key = (payload.api_key or "").strip()
    model = (payload.model or "").strip()
    eff = ai_store.get_effective_config()
    base = base or eff["base_url"]
    key = key or eff.get("api_key", "") or ""
    model = model or eff["model"] or ""
    if not key:
        return ApiResponse(ok=True, data={"models": [], "current": model, "error": "未配置 API key"})
    try:
        models = await _ai_fetch_models(base, key)
    except Exception as exc:
        return ApiResponse(ok=True, data={"models": [], "current": model, "error": str(exc)})
    if model and model not in models:
        models.insert(0, model)
    return ApiResponse(ok=True, data={"models": models, "current": model})


def _ai_reset_router() -> None:
    from app.services.ai.provider_router import ProviderRouter

    ProviderRouter.reset()


@router.post("/ai/providers")
async def create_provider(payload: ProviderPayload):
    """新增自定义 provider。"""
    try:
        return ApiResponse(ok=True, data=ai_store.create_provider(payload.model_dump()))
    except ValueError as exc:
        return _ai_err("INVALID", str(exc), 400)


@router.put("/ai/providers/{provider_id}")
async def update_provider(provider_id: str, payload: ProviderPayload):
    """更新 provider（内置 id → 写入覆盖）。exclude_unset：未提交字段保留原值。"""
    try:
        return ApiResponse(ok=True, data=ai_store.update_provider(provider_id, payload.model_dump(exclude_unset=True)))
    except KeyError as exc:
        return _ai_err("NOT_FOUND", str(exc), 404)


@router.delete("/ai/providers/{provider_id}")
async def delete_provider(provider_id: str):
    """删除自定义 / 重置内置覆盖；返回新的 active_provider_id。"""
    ai_store.delete_provider(provider_id)
    _ai_reset_router()
    return ApiResponse(ok=True, data={"active_provider_id": ai_store.get_active_provider_id() or ""})


@router.post("/ai/providers/{provider_id}/activate")
async def activate_provider(provider_id: str):
    """设为当前（主模型）provider。"""
    try:
        ai_store.set_active_provider(provider_id)
        _ai_reset_router()
        return ApiResponse(ok=True, data={"active_provider_id": provider_id})
    except KeyError as exc:
        return _ai_err("NOT_FOUND", str(exc), 404)


@router.post("/ai/providers/{provider_id}/test")
async def test_provider(provider_id: str):
    """用存储的 provider 配置做一次调用验证。"""
    try:
        cfg = ai_store.get_provider_config(provider_id)
    except KeyError as exc:
        return _ai_err("NOT_FOUND", str(exc), 404)
    return ApiResponse(ok=True, data=await _ai_test_connection(cfg))


@router.post("/ai/test")
async def test_ai_settings(payload: AITestPayload):
    """用提交的配置做一次调用验证（未保存）。"""
    base = (payload.base_url or "").strip()
    key = (payload.api_key or "").strip()
    model = (payload.model or "").strip()
    eff = ai_store.get_effective_config()
    base = base or eff["base_url"]
    key = key or eff.get("api_key", "") or ""
    model = model or eff["model"] or ""
    max_tokens = payload.max_tokens or eff.get("max_tokens")
    temperature = payload.temperature if payload.temperature is not None else eff.get("temperature")
    res = await _ai_test_connection({
        "base_url": base, "api_key": key, "model": model,
        "max_tokens": max_tokens, "temperature": temperature,
    })
    return ApiResponse(ok=True, data=res)
