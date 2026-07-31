import logging
import time
from app.core.config import settings
from app.services.ai.llm_client import LLMClient
from app.core.errors import AIProviderUnavailableError

logger = logging.getLogger(__name__)

_PLACEHOLDER_KEYS = {"", "your_glm_api_key_here", "your_siliconflow_api_key_here", "your_opencodezen_api_key_here", "your_api_key_here"}


class ProviderRouter:
    _singleton_instance = None

    def __new__(cls):
        if cls._singleton_instance is None:
            cls._singleton_instance = super().__new__(cls)
            cls._singleton_instance._initialized = False
        return cls._singleton_instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self.primary = None
        self.fallback = None
        self.tertiary = None

        primary_cfg = settings.ai_provider.get("primary", {})
        fallback_cfg = settings.ai_provider.get("fallback", {})
        tertiary_cfg = settings.ai_provider.get("tertiary", {})

        # Primary: opencodezen
        opencodezen_key = settings.opencodezen_api_key
        if opencodezen_key and opencodezen_key not in _PLACEHOLDER_KEYS:
            try:
                self.primary = LLMClient(
                    api_key=opencodezen_key,
                    base_url=primary_cfg.get("base_url", "https://opencode.ai/zen/v1"),
                    model=primary_cfg.get("model", "gpt-4o-mini"),
                    timeout=primary_cfg.get("timeout_seconds", 15),
                    max_tokens=primary_cfg.get("max_tokens", 512),
                    temperature=primary_cfg.get("temperature", 0.3),
                )
            except Exception as e:
                logger.warning("Primary AI provider (opencodezen) 初始化失败: %s", e)

        # Fallback: glm
        glm_key = settings.glm_api_key
        if glm_key and glm_key not in _PLACEHOLDER_KEYS:
            try:
                self.fallback = LLMClient(
                    api_key=glm_key,
                    base_url=fallback_cfg.get("base_url", "https://open.bigmodel.cn/api/paas/v4"),
                    model=fallback_cfg.get("model", "glm-4.7-flash"),
                    timeout=fallback_cfg.get("timeout_seconds", 15),
                    max_tokens=fallback_cfg.get("max_tokens", 512),
                    temperature=fallback_cfg.get("temperature", 0.3),
                )
            except Exception as e:
                logger.warning("Fallback AI provider (glm) 初始化失败: %s", e)

        # Tertiary: siliconflow
        siliconflow_key = settings.siliconflow_api_key
        if siliconflow_key and siliconflow_key not in _PLACEHOLDER_KEYS:
            try:
                self.tertiary = LLMClient(
                    api_key=siliconflow_key,
                    base_url=tertiary_cfg.get("base_url", "https://api.siliconflow.cn/v1"),
                    model=tertiary_cfg.get("model", "Qwen/Qwen2.5-7B-Instruct"),
                    timeout=tertiary_cfg.get("timeout_seconds", 12),
                    max_tokens=tertiary_cfg.get("max_tokens", 512),
                    temperature=tertiary_cfg.get("temperature", 0.3),
                )
            except Exception as e:
                logger.warning("Tertiary AI provider (siliconflow) 初始化失败: %s", e)

        if not self.primary and not self.fallback and not self.tertiary:
            raise AIProviderUnavailableError("未配置任何可用的 AI Provider，请检查 OPENCODEZEN_API_KEY / GLM_API_KEY / SILICONFLOW_API_KEY 环境变量")

        self.force_json = settings.ai_provider.get("force_json_output", True)

    async def route_request(self, messages: list) -> dict:
        providers = []
        if self.primary:
            providers.append(("opencodezen", self.primary.chat_completion))
        if self.fallback:
            providers.append(("glm", self.fallback.chat_completion))
        if self.tertiary:
            providers.append(("siliconflow", self.tertiary.chat_completion))

        if not providers:
            raise AIProviderUnavailableError("无可用 AI Provider")

        # 顺序调用：primary 优先，失败则 fallback（LLMClient 内部已有重试和超时）
        # 总预算控制：避免多 provider × 多次重试吃光任务超时
        budget = settings.ai_provider.get("route_budget_seconds", 120)
        start = time.monotonic()
        last_error = None
        for name, fn in providers:
            elapsed = time.monotonic() - start
            if elapsed > budget:
                logger.warning("AI Provider 路由预算耗尽 (%.1fs/%ss)，跳过 %s",
                               elapsed, budget, name)
                break
            try:
                result = await fn(messages, self.force_json)
                if result and result.get("content"):
                    return result
            except Exception as e:
                logger.warning("AI Provider %s 调用失败: %s", name, e)
                last_error = e
                continue

        raise AIProviderUnavailableError(f"所有 AI Provider 均不可用: {last_error}")
