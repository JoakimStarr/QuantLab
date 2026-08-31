"""AI 模型设置存储：读写 backend/data/ai_settings.json（从 Quantlerning 移植）。

数据模型（多 provider 化）：
- providers: 用户保存的 provider 条目。内置三家（BUILTIN_PROVIDERS）默认不落盘，被编辑时
  写入同 id 覆盖条目；自定义 provider 使用 prov_ 前缀 id。
- active_provider_id: 当前生效（主模型）provider。
- max_tokens / temperature: 全局生成参数（作用于当前 provider）。
- web_search_key: 联网搜索（Tavily）。

优先级：active provider 的 base_url/api_key/model 覆盖默认；全局生成参数与 web_search_key
覆盖默认；其余字段回退 .env 默认。无任何用户配置时，生效配置 = 默认（与旧版一致）。
API key 只在后端保存；向浏览器返回时打码（masked）。

首次初始化自动从 QuantLab config.yaml 的 ai_provider（primary/fallback/tertiary 或
providers[]）播种 provider 列表，保证旧配置平滑迁移。

自动轮换：active 主 provider 限流（429）或模型不可用时，ProviderRouter 按
get_rotation_providers() 依次尝试其他已配置 api_key 的 provider。
"""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

from app.core.config import is_placeholder_api_key, settings

# PROJECT_ROOT/data/ai_settings.json
DATA_DIR = Path(settings.PROJECT_ROOT) / "data"
SETTINGS_FILE = DATA_DIR / "ai_settings.json"

_lock = threading.Lock()

# 默认值（与旧行为一致：opencodezen 为默认主模型）
DEFAULTS = {
    "base_url": "https://opencode.ai/zen/v1",
    "api_key": "",
    "model": "gpt-4o-mini",
    "max_tokens": 1024,
    "temperature": 0.4,
    # 联网搜索（可选，Tavily）
    "web_search_key": settings.tavily_api_key if hasattr(settings, "tavily_api_key") else "",
}


def _env_key(name: str) -> str:
    """按 provider 逻辑名取 .env 里对应的 key 字段（占位符视为未配置）。"""
    key = getattr(settings, f"{name}_api_key", "") or ""
    return "" if is_placeholder_api_key(key) else key


# 内置三家默认 provider（OpenAI 兼容接口）。opencodezen 居首，保持「默认主模型」行为不变。
# 附带 .env 中已有的 key，保证旧的三家（glm/siliconflow 备用）开箱即用。
BUILTIN_PROVIDERS = [
    {
        "id": "builtin_opencodezen",
        "name": "OpenCodeZen",
        "base_url": "https://opencode.ai/zen/v1",
        "model": "gpt-4o-mini",
    },
    {
        "id": "builtin_glm",
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4.7-flash",
    },
    {
        "id": "builtin_siliconflow",
        "name": "硅基流动 SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen2.5-7B-Instruct",
    },
]

# provider 逻辑名 -> 内置 id
_BUILTIN_ID_BY_NAME = {b["id"]: b for b in BUILTIN_PROVIDERS}
_LOGICAL_TO_ID = {
    "opencodezen": "builtin_opencodezen",
    "glm": "builtin_glm",
    "siliconflow": "builtin_siliconflow",
}

# 全局参数（顶层保存字段）
_GLOBAL_KEYS = ("max_tokens", "temperature", "web_search_key")


def _load() -> dict:
    """读取 JSON（须在持锁下调用）。文件不存在时从 QuantLab config.yaml 播种。"""
    if not SETTINGS_FILE.exists():
        saved = _seed_from_quantlab_config()
        _write(saved)
        return saved
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8")) or {}
    except (json.JSONDecodeError, OSError):
        return {}
    if "providers" not in saved:
        saved = _migrate_legacy(saved)
        _write(saved)
    return saved


def _write(saved: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(
        json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------- 播种 / 迁移 ----------

def _seed_from_quantlab_config() -> dict:
    """从 config.yaml ai_provider（primary/fallback/tertiary 或 providers[]）播种.

    若 config.yaml 无 ai_provider（全新安装），则返回空结构，由 BUILTIN_PROVIDERS 兜底。
    """
    saved: dict = {}
    ai = getattr(settings, "ai_provider", None)
    if ai is None:
        return saved
    cfg = ai.model_dump() if hasattr(ai, "model_dump") else {}
    ps = cfg.get("providers") or []
    main = (cfg.get("main_provider") or "").strip()
    if not ps:
        return saved

    providers = []
    active = None
    for p in ps:
        if not isinstance(p, dict):
            continue
        name = (p.get("provider") or "").strip()
        if not name:
            continue
        base = (p.get("base_url") or "").strip()
        model = (p.get("model") or "").strip()
        bid = _LOGICAL_TO_ID.get(name)
        key = _env_key(name) if not bid else _env_key(name)
        entry = {
            "id": bid or ("prov_" + uuid.uuid4().hex[:10]),
            "name": name,
            "base_url": base,
            "model": model,
        }
        if key:
            entry["api_key"] = key
        providers.append(entry)
        if name == main and not active:
            active = entry["id"]
    saved["providers"] = providers
    saved["active_provider_id"] = active or (providers[0]["id"] if providers else BUILTIN_PROVIDERS[0]["id"])
    for gk in ("max_tokens", "temperature"):
        if cfg.get(gk) is not None:
            saved[gk] = cfg[gk]
    return saved


def _migrate_legacy(saved: dict) -> dict:
    """把旧版「主/备用」或 Quantlerning 平铺格式迁移为 provider 列表。"""
    main_url = (saved.get("base_url") or "").strip()
    main_key = saved.get("api_key") or ""
    main_model = (saved.get("model") or "").strip()
    fb_url = (saved.get("fallback_base_url") or saved.get("fallback_url") or "").strip()
    fb_key = saved.get("fallback_api_key") or main_key
    fb_model = (saved.get("fallback_model") or "").strip()

    providers: list[dict] = []
    active_id: str | None = None
    if main_url:
        entry, eid = _legacy_entry("主模型", main_url, main_model, main_key)
        providers.append(entry)
        active_id = eid
    if fb_url:
        entry, eid = _legacy_entry("备用模型", fb_url, fb_model, fb_key)
        providers.append(entry)
        active_id = active_id or eid
    if not providers:
        # 完全是 Quantlerning providers 数组格式但缺标识 → 原样保留
        providers = saved.get("providers") or []
        active_id = saved.get("active_provider_id")
    saved["providers"] = providers
    saved["active_provider_id"] = active_id or (providers[0]["id"] if providers else BUILTIN_PROVIDERS[0]["id"])
    return saved


def _legacy_entry(name: str, base_url: str, model: str, api_key: str) -> tuple[dict, str | None]:
    for b in BUILTIN_PROVIDERS:
        if b["base_url"].rstrip("/") == base_url.rstrip("/"):
            return {
                "id": b["id"],
                "name": b["name"],
                "base_url": base_url,
                "model": model or b["model"],
                **({"api_key": api_key} if api_key else {}),
            }, b["id"]
    entry: dict = {
        "id": "prov_legacy_" + uuid.uuid4().hex[:6],
        "name": name,
        "base_url": base_url,
    }
    if model:
        entry["model"] = model
    if api_key:
        entry["api_key"] = api_key
    return entry, None


def _builtin_with_key(b: dict) -> dict:
    """内置条目附带 .env key（未落盘时也能使旧三家开箱即用）。"""
    item = dict(b)
    logical = _BUILTIN_ID_BY_NAME.get(b["id"])  # no-op
    name_of_id = {
        "builtin_opencodezen": "opencodezen",
        "builtin_glm": "glm",
        "builtin_siliconflow": "siliconflow",
    }.get(b["id"])
    key = _env_key(name_of_id) if name_of_id else ""
    if key:
        item["api_key"] = key
    return item


# ---------- 读取 ----------

def get_providers() -> list[dict]:
    """合并后的 provider 列表：内置在前，自定义追加在后；同 id 覆盖内置。

    每个条目含 id/name/base_url/model/api_key/builtin。
    """
    with _lock:
        saved = _load()
        stored = {p["id"]: p for p in saved.get("providers") or []}
    merged: list[dict] = []
    for b in BUILTIN_PROVIDERS:
        item = _builtin_with_key(b)
        if b["id"] in stored:
            item.update({k: v for k, v in stored[b["id"]].items() if v not in (None, "")})
        item["builtin"] = True
        merged.append(item)
    for p in stored.values():
        if any(p["id"] == m["id"] for m in merged):
            continue
        item = dict(p)
        item.setdefault("base_url", "")
        item.setdefault("model", "")
        item["builtin"] = False
        merged.append(item)
    return merged


def get_active_provider_id() -> str | None:
    with _lock:
        saved = _load()
        active = saved.get("active_provider_id")
    if active:
        return active
    providers = get_providers()
    return providers[0]["id"] if providers else None


def get_active_provider() -> dict | None:
    providers = get_providers()
    if not providers:
        return None
    active_id = get_active_provider_id()
    for p in providers:
        if p["id"] == active_id:
            return p
    return providers[0]


def get_provider_config(provider_id: str) -> dict:
    """返回指定 provider 的完整请求配置（含全局 max_tokens/temperature），供测试/路由使用。"""
    providers = get_providers()
    p = next((x for x in providers if x["id"] == provider_id), None)
    if p is None:
        raise KeyError(f"provider 不存在: {provider_id}")
    with _lock:
        saved = _load()
    cfg = dict(DEFAULTS)
    for k in _GLOBAL_KEYS:
        if saved.get(k) not in (None, ""):
            cfg[k] = saved[k]
    for k in ("base_url", "api_key", "model"):
        if p.get(k):
            cfg[k] = p[k]
    # per-provider max_tokens 覆盖全局（推理模型需要更大预算，非推理模型保持小值省成本）
    if p.get("max_tokens") not in (None, ""):
        cfg["max_tokens"] = p["max_tokens"]
    cfg["provider_id"] = p["id"]
    cfg["provider"] = p.get("name") or p["id"]
    if not cfg.get("api_key"):
        cfg["api_key"] = _env_key("opencodezen") or ""
    return _normalize(cfg)


def get_effective_config() -> dict:
    """当前生效的完整 AI 配置（active provider + 全局参数 + 默认兜底）。"""
    prov = get_active_provider()
    with _lock:
        saved = _load()
    cfg = dict(DEFAULTS)
    for k in _GLOBAL_KEYS:
        if saved.get(k) not in (None, ""):
            cfg[k] = saved[k]
    if prov:
        for k in ("base_url", "api_key", "model"):
            if prov.get(k):
                cfg[k] = prov[k]
    if prov and prov.get("max_tokens") not in (None, ""):
        cfg["max_tokens"] = prov["max_tokens"]
    if not cfg.get("api_key"):
        cfg["api_key"] = _env_key("opencodezen") or ""
    return _normalize(cfg)


def get_rotation_providers() -> list[dict]:
    """active 之外、已配置 api_key 的 provider 配置列表（按列表顺序）。"""
    active_id = get_active_provider_id()
    return [
        {
            "provider": p["name"],
            "base_url": p["base_url"],
            "model": p["model"],
            "api_key": p["api_key"],
        }
        for p in get_providers()
        if p["id"] != active_id and p.get("api_key")
    ]


# ---------- 写入 ----------

def save_global_config(payload: dict) -> dict:
    """保存全局参数。web_search_key：未提供→保留；空串→清除；非空→更新。"""
    with _lock:
        saved = _load()
        if "max_tokens" in payload and payload["max_tokens"] is not None:
            saved["max_tokens"] = payload["max_tokens"]
        if "temperature" in payload and payload["temperature"] is not None:
            saved["temperature"] = payload["temperature"]
        if "web_search_key" in payload:
            v = payload["web_search_key"]
            if v is None:
                pass
            elif str(v).strip() == "":
                saved.pop("web_search_key", None)
            else:
                saved["web_search_key"] = str(v).strip()
        _write(saved)
    return get_effective_config()


def create_provider(payload: dict) -> dict:
    name = (payload.get("name") or "").strip()
    base_url = (payload.get("base_url") or "").strip()
    model = (payload.get("model") or "").strip()
    if not name or not base_url or not model:
        raise ValueError("name / base_url / model 均不能为空")
    entry: dict = {"id": "prov_" + uuid.uuid4().hex[:10], "name": name, "base_url": base_url, "model": model}
    api_key = (payload.get("api_key") or "").strip()
    if api_key:
        entry["api_key"] = api_key
    if payload.get("max_tokens") not in (None, ""):
        entry["max_tokens"] = int(payload["max_tokens"])
    with _lock:
        saved = _load()
        saved.setdefault("providers", []).append(entry)
        _write(saved)
    return _public_provider(entry, builtin=False)


def update_provider(provider_id: str, payload: dict) -> dict:
    with _lock:
        saved = _load()
        providers = saved.setdefault("providers", [])
        entry = next((p for p in providers if p["id"] == provider_id), None)
        builtin = next((b for b in BUILTIN_PROVIDERS if b["id"] == provider_id), None)
        if entry is None:
            if builtin is None:
                raise KeyError(f"provider 不存在: {provider_id}")
            entry = {"id": builtin["id"], "name": builtin["name"], "base_url": builtin["base_url"], "model": builtin["model"]}
            providers.append(entry)
        for field in ("name", "base_url", "model"):
            if field not in payload:
                continue
            v = payload[field]
            if v is None:
                continue
            v = str(v).strip()
            if v:
                entry[field] = v
        if "max_tokens" in payload:
            v = payload["max_tokens"]
            if v is None or str(v).strip() == "":
                entry.pop("max_tokens", None)
            else:
                try:
                    entry["max_tokens"] = int(v)
                except (TypeError, ValueError):
                    raise ValueError("max_tokens 必须为整数") from None
        if "api_key" in payload:
            v = payload["api_key"]
            if v is None:
                pass
            elif str(v).strip() == "":
                entry.pop("api_key", None)
            else:
                entry["api_key"] = str(v).strip()
        _write(saved)
    merged = next(p for p in get_providers() if p["id"] == provider_id)
    return _public_provider(merged, builtin=bool(merged.get("builtin")))


def delete_provider(provider_id: str) -> str:
    with _lock:
        saved = _load()
        saved["providers"] = [p for p in saved.get("providers") or [] if p["id"] != provider_id]
        if saved.get("active_provider_id") == provider_id:
            saved.pop("active_provider_id", None)
        _write(saved)
    return get_active_provider_id() or ""


def set_active_provider(provider_id: str) -> str:
    ids = {p["id"] for p in get_providers()}
    if provider_id not in ids:
        raise KeyError(f"provider 不存在: {provider_id}")
    with _lock:
        saved = _load()
        saved["active_provider_id"] = provider_id
        _write(saved)
    return provider_id


# ---------- 展示 ----------

def _public_provider(p: dict, builtin: bool) -> dict:
    key_ok = bool(p.get("api_key")) and not is_placeholder_api_key(p.get("api_key", ""))
    return {
        "id": p["id"],
        "name": p.get("name", ""),
        "base_url": p.get("base_url", ""),
        "model": p.get("model", ""),
        "api_key_masked": mask_key(p.get("api_key", "")),
        "configured": key_ok,
        "builtin": builtin,
        "max_tokens": p.get("max_tokens"),
    }


def public_config() -> dict:
    """公开配置：providers 列表 + 全局生成参数（max_tokens/temperature）。

    注意 max_tokens 返回**全局**值（非 active provider 的生效值）——per-provider
    覆盖只影响路由，前端「生成参数」滑块改动的是全局兜底。
    """
    with _lock:
        saved = _load()
    global_max_tokens = saved.get("max_tokens")
    if global_max_tokens in (None, ""):
        global_max_tokens = DEFAULTS["max_tokens"]
    try:
        global_max_tokens = int(global_max_tokens)
    except (TypeError, ValueError):
        global_max_tokens = int(DEFAULTS["max_tokens"])
    global_temp = saved.get("temperature")
    if global_temp in (None, ""):
        global_temp = DEFAULTS["temperature"]
    try:
        global_temp = float(global_temp)
    except (TypeError, ValueError):
        global_temp = float(DEFAULTS["temperature"])
    return {
        "providers": [_public_provider(p, builtin=bool(p.get("builtin"))) for p in get_providers()],
        "active_provider_id": get_active_provider_id() or "",
        "max_tokens": global_max_tokens,
        "temperature": global_temp,
        "web_search_key_masked": mask_key(saved.get("web_search_key", "")),
        "web_search_configured": bool(saved.get("web_search_key")),
    }


def mask_key(key: str) -> str:
    key = key or ""
    if len(key) <= 12:
        return "***" if key else ""
    return f"{key[:6]}***{key[-4:]}"


def _normalize(cfg: dict) -> dict:
    try:
        cfg["max_tokens"] = int(cfg["max_tokens"] or 4096)
    except (TypeError, ValueError):
        cfg["max_tokens"] = 4096
    try:
        cfg["temperature"] = float(cfg["temperature"] if cfg.get("temperature") is not None else 0.4)
    except (TypeError, ValueError):
        cfg["temperature"] = 0.4
    cfg["temperature"] = max(0.0, min(2.0, cfg["temperature"]))
    return cfg
