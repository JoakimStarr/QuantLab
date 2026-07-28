import asyncio
import logging
from app.core.config import settings
from app.services.ai.llm_client import LLMClient
from app.core.errors import AIProviderUnavailableError

logger = logging.getLogger(__name__)

_PLACEHOLDER_KEYS = {"", "your_glm_api_key_here", "your_siliconflow_api_key_here", "your_api_key_here"}


class ProviderRouter:
    def __init__(self):
        self.primary = None
        self.fallback = None

        primary_cfg = settings.ai_provider.get("primary", {})
        fallback_cfg = settings.ai_provider.get("fallback", {})
        primary_key = settings.glm_api_key
        fallback_key = settings.siliconflow_api_key

        if primary_key and primary_key not in _PLACEHOLDER_KEYS:
            try:
                self.primary = LLMClient(
                    api_key=primary_key,
                    base_url=primary_cfg["base_url"],
                    model=primary_cfg["model"],
                    timeout=primary_cfg.get("timeout_seconds", 15),
                    max_tokens=primary_cfg.get("max_tokens", 512),
                    temperature=primary_cfg.get("temperature", 0.3),
                )
            except Exception as e:
                logger.warning("Primary AI provider 初始化失败: %s", e)

        if fallback_key and fallback_key not in _PLACEHOLDER_KEYS:
            try:
                self.fallback = LLMClient(
                    api_key=fallback_key,
                    base_url=fallback_cfg["base_url"],
                    model=fallback_cfg["model"],
                    timeout=fallback_cfg.get("timeout_seconds", 15),
                    max_tokens=fallback_cfg.get("max_tokens", 512),
                    temperature=fallback_cfg.get("temperature", 0.3),
                )
            except Exception as e:
                logger.warning("Fallback AI provider 初始化失败: %s", e)

        if not self.primary and not self.fallback:
            raise AIProviderUnavailableError("未配置任何可用的 AI Provider，请检查 GLM_API_KEY / SILICONFLOW_API_KEY 环境变量")

        self.force_json = settings.ai_provider.get("force_json_output", True)

    async def route_request(self, messages: list) -> dict:
        providers = []
        if self.primary:
            providers.append(("primary", self.primary.chat_completion))
        if self.fallback:
            providers.append(("fallback", self.fallback.chat_completion))

        if not providers:
            raise AIProviderUnavailableError("无可用 AI Provider")

        # 顺序调用：primary 优先，失败则 fallback（LLMClient 内部已有重试和超时）
        last_error = None
        for name, fn in providers:
            try:
                result = await fn(messages, self.force_json)
                if result and result.get("content"):
                    return result
            except Exception as e:
                logger.warning("AI Provider %s 调用失败: %s", name, e)
                last_error = e
                continue

        raise AIProviderUnavailableError(f"所有 AI Provider 均不可用: {last_error}")
