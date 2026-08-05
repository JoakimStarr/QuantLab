import logging
import time

from app.core.config import is_placeholder_api_key, settings
from app.core.errors import AIProviderUnavailableError
from app.services.ai.llm_client import LLMClient

logger = logging.getLogger(__name__)


class ProviderRouter:
    _singleton_instance = None

    def __new__(cls):
        if cls._singleton_instance is None:
            cls._singleton_instance = super().__new__(cls)
            cls._singleton_instance._initialized = False
        return cls._singleton_instance

    def __init__(self):
        # 单例缓存：仅在已成功初始化（至少一个 provider 可用）时跳过重新初始化。
        # 如果上次初始化失败（所有 provider 都不可用），允许本次重试——
        # 避免 .env 延迟加载或服务启动顺序导致单例被永久锁死为"无可用 Provider"。
        if getattr(self, "_initialized", False) and (self.primary or self.fallback or self.tertiary):
            return
        self._initialized = True
        self.primary = None
        self.fallback = None
        self.tertiary = None

        primary_cfg = settings.ai_provider.get("primary", {})
        fallback_cfg = settings.ai_provider.get("fallback", {})
        tertiary_cfg = settings.ai_provider.get("tertiary", {})

        # 收集各 provider 初始化失败原因，便于在最终错误中暴露给用户
        init_errors: list[str] = []

        # Primary: opencodezen
        opencodezen_key = settings.opencodezen_api_key
        if opencodezen_key and not is_placeholder_api_key(opencodezen_key):
            try:
                self.primary = LLMClient(
                    api_key=opencodezen_key,
                    base_url=primary_cfg.get("base_url") or "https://opencode.ai/zen/v1",
                    model=primary_cfg.get("model") or "gpt-4o-mini",
                    timeout=primary_cfg.get("timeout_seconds", 15),
                    max_tokens=primary_cfg.get("max_tokens", 512),
                    temperature=primary_cfg.get("temperature", 0.3),
                )
                logger.info(
                    "Primary AI provider (opencodezen) 已就绪: model=%s, key=%s...",
                    self.primary.model,
                    opencodezen_key[:8],
                )
            except Exception as e:
                logger.warning("Primary AI provider (opencodezen) 初始化失败: %s", e)
                init_errors.append(f"opencodezen: {e}")

        # Fallback: glm
        glm_key = settings.glm_api_key
        if glm_key and not is_placeholder_api_key(glm_key):
            try:
                self.fallback = LLMClient(
                    api_key=glm_key,
                    base_url=fallback_cfg.get("base_url") or "https://open.bigmodel.cn/api/paas/v4",
                    model=fallback_cfg.get("model") or "glm-4.7-flash",
                    timeout=fallback_cfg.get("timeout_seconds", 15),
                    max_tokens=fallback_cfg.get("max_tokens", 512),
                    temperature=fallback_cfg.get("temperature", 0.3),
                )
                logger.info(
                    "Fallback AI provider (glm) 已就绪: model=%s, key=%s...",
                    self.fallback.model,
                    glm_key[:8],
                )
            except Exception as e:
                logger.warning("Fallback AI provider (glm) 初始化失败: %s", e)
                init_errors.append(f"glm: {e}")

        # Tertiary: siliconflow
        siliconflow_key = settings.siliconflow_api_key
        if siliconflow_key and not is_placeholder_api_key(siliconflow_key):
            try:
                self.tertiary = LLMClient(
                    api_key=siliconflow_key,
                    base_url=tertiary_cfg.get("base_url") or "https://api.siliconflow.cn/v1",
                    model=tertiary_cfg.get("model") or "Qwen/Qwen2.5-7B-Instruct",
                    timeout=tertiary_cfg.get("timeout_seconds", 12),
                    max_tokens=tertiary_cfg.get("max_tokens", 512),
                    temperature=tertiary_cfg.get("temperature", 0.3),
                )
                logger.info(
                    "Tertiary AI provider (siliconflow) 已就绪: model=%s, key=%s...",
                    self.tertiary.model,
                    siliconflow_key[:8],
                )
            except Exception as e:
                logger.warning("Tertiary AI provider (siliconflow) 初始化失败: %s", e)
                init_errors.append(f"siliconflow: {e}")

        if not self.primary and not self.fallback and not self.tertiary:
            # 重置 _initialized，允许下次调用时重试
            # （.env 可能延迟加载，或用户刚配置了 key）
            self._initialized = False
            key_status = (
                f"opencodezen={'有' if opencodezen_key and not is_placeholder_api_key(opencodezen_key) else '无'}, "
                f"glm={'有' if glm_key and not is_placeholder_api_key(glm_key) else '无'}, "
                f"siliconflow={'有' if siliconflow_key and not is_placeholder_api_key(siliconflow_key) else '无'}"
            )
            detail = f"当前 key 状态: {key_status}"
            if init_errors:
                detail += "。初始化失败原因: " + "; ".join(init_errors)
            raise AIProviderUnavailableError(
                "未配置任何可用的 AI Provider，请检查 OPENCODEZEN_API_KEY / GLM_API_KEY / SILICONFLOW_API_KEY 环境变量。"
                + detail
            )

        self.force_json = settings.ai_provider.get("force_json_output", True)

    async def route_request(self, messages: list, force_json: bool = None) -> dict:
        providers = []
        if self.primary:
            providers.append(("opencodezen", self.primary.chat_completion))
        if self.fallback:
            providers.append(("glm", self.fallback.chat_completion))
        if self.tertiary:
            providers.append(("siliconflow", self.tertiary.chat_completion))

        if not providers:
            raise AIProviderUnavailableError("无可用 AI Provider")

        # 覆盖全局 force_json（如追问对话等需要自由文本输出时传 False）
        use_json = self.force_json if force_json is None else force_json

        # 顺序调用：primary 优先，失败则 fallback（LLMClient 内部已有重试和超时）
        # 总预算控制：避免多 provider × 多次重试吃光任务超时
        budget = settings.ai_provider.get("route_budget_seconds", 120)
        start = time.monotonic()
        last_error = None
        for name, fn in providers:
            elapsed = time.monotonic() - start
            if elapsed > budget:
                logger.warning("AI Provider 路由预算耗尽 (%.1fs/%ss)，跳过 %s", elapsed, budget, name)
                break
            try:
                result = await fn(messages, use_json)
                if result and result.get("content"):
                    return result
            except Exception as e:
                logger.warning("AI Provider %s 调用失败: %s", name, e)
                last_error = e
                continue

        raise AIProviderUnavailableError(f"所有 AI Provider 均不可用: {last_error}")
