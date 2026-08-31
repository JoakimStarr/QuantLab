import hashlib
import logging
import time

from app.core.config import is_placeholder_api_key, settings
from app.core.errors import AIProviderUnavailableError
from app.services.ai import ai_settings_store as store
from app.services.ai.llm_client import LLMClient

logger = logging.getLogger(__name__)


def _key_fingerprint(key: str) -> str:
    """密钥指纹：sha256 前 8 位。用于日志排查配置是否生效，不可逆推原 key。"""
    return hashlib.sha256(key.encode()).hexdigest()[:8]


class ProviderCircuitBreaker:
    """Provider 熔断器：连续失败 N 次后熔断一段时间，避免每次任务都从头
    撞同一个死 provider、把路由预算吃光后 fallback 没机会执行。

    状态机：closed → open（熔断，跳过）→ half-open（探活）→ closed（恢复）。
    429 限流/401 鉴权/超时都算失败；连续成功一次即复位计数。
    """

    def __init__(self, name: str, failure_threshold: int = 3, cooldown_seconds: int = 120):
        self.name = name
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(1, cooldown_seconds)
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    def allow_request(self) -> bool:
        if self._opened_at is None:
            return True
        if time.monotonic() - self._opened_at >= self.cooldown_seconds:
            return True
        return False

    def record_success(self) -> None:
        self._consecutive_failures = 0
        if self._opened_at is not None:
            logger.info("AI Provider %s 熔断恢复（探活成功）", self.name)
        self._opened_at = None

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold and self._opened_at is None:
            self._opened_at = time.monotonic()
            logger.warning(
                "AI Provider %s 连续失败 %d 次，熔断 %.0fs",
                self.name, self._consecutive_failures, self.cooldown_seconds,
            )

    @property
    def is_open(self) -> bool:
        return self._opened_at is not None and not self.allow_request()


class ProviderRouter:
    _singleton_instance = None

    def __new__(cls):
        if cls._singleton_instance is None:
            cls._singleton_instance = super().__new__(cls)
            cls._singleton_instance._initialized = False
        return cls._singleton_instance

    @classmethod
    def reset(cls) -> None:
        """丢弃单例，下次访问时按最新 store 配置重新初始化。"""
        cls._singleton_instance = None

    def __init__(self):
        if getattr(self, "_initialized", False) and self._route_order:
            return
        self._initialized = True
        self._route_order: list = []

        # 读取 store：active（主模型）优先，其余按 provider 列表顺序回退；
        # 仅纳入已配置自有 api_key（非占位符）的 provider。
        providers = store.get_providers()
        active_id = store.get_active_provider_id() or ""
        providers_sorted = sorted(providers, key=lambda p: 0 if p["id"] == active_id else 1)

        # 配置可调熔断参数（config.yaml ai_provider.circuit_breaker）
        ai_cfg = settings.ai_provider.model_dump() if hasattr(settings.ai_provider, "model_dump") else {}
        br_cfg = ai_cfg.get("circuit_breaker") or {}
        if hasattr(br_cfg, "model_dump"):
            br_cfg = br_cfg.model_dump()
        self._breakers: dict[str, ProviderCircuitBreaker] = {}
        br_threshold = int(br_cfg.get("failure_threshold", 3))
        br_cooldown = int(br_cfg.get("cooldown_seconds", 120))

        # 收集各 provider 初始化失败原因，便于在最终错误中暴露给用户
        init_errors: list[str] = []
        for p in providers_sorted:
            pid = p["id"]
            key = (p.get("api_key") or "").strip()
            if not key or is_placeholder_api_key(key):
                continue
            try:
                cfg = store.get_provider_config(pid)
            except KeyError:
                continue
            name = cfg.get("provider") or pid
            model = cfg.get("model") or p.get("model") or ""
            if not model:
                continue
            try:
                client = LLMClient(
                    api_key=key,
                    base_url=cfg.get("base_url") or "https://api.example.com/v1",
                    model=model,
                    timeout=int(cfg.get("timeout_seconds", 30)),
                    max_tokens=int(cfg.get("max_tokens") or 512),
                    temperature=float(cfg.get("temperature", 0.4)),
                )
                self._breakers[pid] = ProviderCircuitBreaker(pid, br_threshold, br_cooldown)
                entry = {"name": name, "provider": name, "model": model, "client": client}
                self._route_order.append(entry)
                logger.info(
                    "AI provider[%s](%s) 就绪: model=%s key=fp:%s",
                    pid, name, client.model, _key_fingerprint(key),
                )
            except Exception as e:
                logger.warning("AI provider[%s] 初始化失败: %s", pid, e)
                init_errors.append(f"{pid}: {e}")

        if not self._route_order:
            # 重置 _initialized，允许下次调用时重试
            self._initialized = False
            configured = [p.get("name") or p["id"] for p in providers if p.get("api_key")]
            detail = f"已配置 key 的 provider: {configured or '无'}"
            if init_errors:
                detail += "。初始化失败原因: " + "; ".join(init_errors)
            raise AIProviderUnavailableError(
                "未配置任何可用的 AI Provider，请在设置页配置 provider 与 API Key。" + detail
            )

        self.force_json = ai_cfg.get("force_json_output", True)

    async def route_request(self, messages: list, force_json: bool = None) -> dict:
        providers = list(self._route_order)

        if not providers:
            raise AIProviderUnavailableError("无可用 AI Provider")

        # 主 provider 优先，失败则按列表顺序回退；LLMClient 内部已有重试和超时
        use_json = self.force_json if force_json is None else force_json

        budget = settings.ai_provider.get("route_budget_seconds", 120)
        start = time.monotonic()
        last_error = None
        for entry in providers:
            name = entry["provider"]
            client = entry["client"]
            pid = entry.get("name")
            elapsed = time.monotonic() - start
            if elapsed > budget:
                logger.warning("AI Provider 路由预算耗尽 (%.1fs/%ss)，跳过 %s", elapsed, budget, name)
                break
            breaker = self._breakers.get(pid or name)
            if breaker is not None and breaker.is_open:
                logger.info("AI Provider %s 处于熔断期，跳过", name)
                last_error = last_error or RuntimeError(f"{name} 熔断中")
                continue
            try:
                result = await client.chat_completion(messages, use_json)
                if result and result.get("content"):
                    if breaker is not None:
                        breaker.record_success()
                    return result
            except Exception as e:
                logger.warning("AI Provider %s 调用失败: %s", name, e)
                if breaker is not None:
                    breaker.record_failure()
                last_error = e
                continue

        raise AIProviderUnavailableError(f"所有 AI Provider 均不可用: {last_error}")