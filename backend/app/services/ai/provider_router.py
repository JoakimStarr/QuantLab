import asyncio
from app.core.config import settings
from app.services.ai.llm_client import LLMClient
from app.core.errors import AIProviderUnavailableError


class ProviderRouter:
    def __init__(self):
        primary_cfg = settings.ai_provider["primary"]
        fallback_cfg = settings.ai_provider["fallback"]
        primary_key = settings.glm_api_key
        fallback_key = settings.siliconflow_api_key
        self.primary = LLMClient(api_key=primary_key, base_url=primary_cfg["base_url"], model=primary_cfg["model"], timeout=primary_cfg.get("timeout_seconds", 8), max_tokens=primary_cfg.get("max_tokens", 512), temperature=primary_cfg.get("temperature", 0.3))
        self.fallback = LLMClient(api_key=fallback_key, base_url=fallback_cfg["base_url"], model=fallback_cfg["model"], timeout=fallback_cfg.get("timeout_seconds", 12), max_tokens=fallback_cfg.get("max_tokens", 512), temperature=fallback_cfg.get("temperature", 0.3))
        self.total_timeout = settings.ai_provider.get("total_timeout_seconds", 10)
        self.force_json = settings.ai_provider.get("force_json_output", True)

    async def route_request(self, messages: list) -> dict:
        async def call_primary():
            return await self.primary.chat_completion(messages, self.force_json)

        async def call_fallback():
            return await self.fallback.chat_completion(messages, self.force_json)

        tasks = [asyncio.create_task(call_primary()), asyncio.create_task(call_fallback())]
        done, pending = await asyncio.wait(tasks, timeout=self.total_timeout, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            try:
                result = task.result()
                if result and result.get("content"):
                    return result
            except Exception:
                continue
        last_error = None
        for task in [call_primary, call_fallback]:
            try:
                return await task()
            except Exception as e:
                last_error = e
                continue
        raise AIProviderUnavailableError(f"所有 AI Provider 均不可用: {last_error}")