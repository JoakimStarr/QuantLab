"""主模型动态路由测试（store 版）：active provider 优先 + 其余按列表顺序回退。

ProviderRouter 现在读 ai_settings_store：get_providers / get_active_provider_id /
get_provider_config。测试通过 monkeypatch 这些函数隔离真实 store。
"""
import types

import pytest

from app.services.ai import ai_settings_store as store
from app.services.ai import provider_router as pr_mod


def _install(monkeypatch, providers, active, configs):
    def gcfg(pid):
        if pid not in configs:
            raise KeyError(pid)
        return configs[pid]

    monkeypatch.setattr(store, "get_providers", lambda: providers)
    monkeypatch.setattr(store, "get_active_provider_id", lambda: active)
    monkeypatch.setattr(store, "get_provider_config", gcfg)
    monkeypatch.setattr(pr_mod, "settings", types.SimpleNamespace(ai_provider={}))
    pr_mod.ProviderRouter.reset()


def _cfg(pid, **over):
    d = {"provider": pid, "provider_id": pid, "base_url": f"http://x/{pid}",
         "api_key": f"sk_{pid}", "model": f"m{pid}", "max_tokens": 512, "temperature": 0.3}
    d.update(over)
    return d


def _prov(pid, name=None, has_key=True):
    p = {"id": pid, "name": name or pid, "base_url": f"http://x/{pid}",
         "model": f"m{pid}", "builtin": False}
    if has_key:
        p["api_key"] = f"sk_{pid}"
    return p


def _names(r):
    return [e["provider"] for e in r._route_order]


def test_active_provider_first(monkeypatch):
    providers = [_prov("a"), _prov("b"), _prov("c")]
    configs = {p: _cfg(p) for p in ("a", "b", "c")}
    _install(monkeypatch, providers, "b", configs)
    r = pr_mod.ProviderRouter()
    assert _names(r) == ["b", "a", "c"]


def test_no_key_provider_skipped(monkeypatch):
    providers = [_prov("a", has_key=True), _prov("b", has_key=False)]
    configs = {"a": _cfg("a"), "b": _cfg("b", api_key="")}
    _install(monkeypatch, providers, "a", configs)
    r = pr_mod.ProviderRouter()
    assert _names(r) == ["a"]


def test_custom_provider_active(monkeypatch):
    providers = [_prov("prov_1", name="自定义", has_key=True)]
    configs = {"prov_1": _cfg("prov_1", provider="自定义", model="my-model", base_url="http://custom/v1")}
    _install(monkeypatch, providers, "prov_1", configs)
    r = pr_mod.ProviderRouter()
    assert _names(r) == ["自定义"]
    assert r._route_order[0]["model"] == "my-model"


def test_reset_uses_latest_config(monkeypatch):
    providers = [_prov("a"), _prov("b"), _prov("c")]
    configs = {p: _cfg(p) for p in ("a", "b", "c")}
    _install(monkeypatch, providers, "a", configs)
    assert _names(pr_mod.ProviderRouter())[0] == "a"
    # 切换 active 后 reset 重建
    monkeypatch.setattr(store, "get_active_provider_id", lambda: "c")
    pr_mod.ProviderRouter.reset()
    assert _names(pr_mod.ProviderRouter())[0] == "c"


def test_missing_provider_config_skipped(monkeypatch):
    # config 缺失的 active 不会使路由崩溃，回退到其他可用 provider
    providers = [_prov("a"), _prov("b")]
    configs = {"b": _cfg("b")}
    _install(monkeypatch, providers, "a", configs)
    r = pr_mod.ProviderRouter()
    assert _names(r) == ["b"]