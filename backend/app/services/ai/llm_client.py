import json
from json_repair import repair_json
import asyncio
import logging
import httpx
from openai import AsyncOpenAI
import openai
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log
from app.core.errors import AIProviderUnavailableError, AINotConfiguredError

logger = logging.getLogger(__name__)


def _extract_json(text: str):
    """从 LLM 返回文本中提取 JSON，使用 json-repair 处理常见格式问题。"""
    repaired = repair_json(text, return_objects=True)
    if repaired is not None:
        return repaired
    # 尝试原始解析作为兜底
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    raise json.JSONDecodeError("无法从返回文本中提取 JSON", text, 0)


class LLMClient:
    def __init__(self, api_key: str, base_url: str, model: str, timeout: int = 15,
                 max_tokens: int = 512, temperature: float = 0.3):
        if not api_key:
            raise AINotConfiguredError(f"API Key 未配置 for {model}")
        self.api_key = api_key
        # base_url 末尾必须保留斜杠：openai SDK 用 httpx.URL.join() 拼接路径，
        # 若末尾无斜杠，按 RFC3986 最后一段会被替换（如 .../v4 -> .../chat/completions），导致 404
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature
        # AsyncOpenAI 客户端：base_url 应为基础 URL（不含 /chat/completions），SDK 自动拼接
        # trust_env=False：忽略 HTTP_PROXY/ALL_PROXY 等环境变量，直连 API。
        # 原因：用户 shell 可能设了 ALL_PROXY=socks://...（Clash/V2Ray），
        # httpx 不支持 socks:// scheme 会直接报错 "Unknown scheme for proxy URL"，
        # 且所有 AI provider（opencodezen/glm/siliconflow）均在 NO_PROXY 内或为国内服务，
        # 无需走代理。
        self._http_client = httpx.AsyncClient(trust_env=False, timeout=timeout)
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=self.base_url,
            timeout=timeout,
            http_client=self._http_client,
        )

    async def chat_completion(self, messages: list, force_json: bool = True) -> dict:
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        # 免费模型可能不支持 response_format，先尝试带
        use_response_format = force_json
        if use_response_format:
            kwargs["response_format"] = {"type": "json_object"}

        last_error = None
        # 外层最多 2 次：第一次带 response_format，不支持则降级重试
        for attempt in range(2):
            try:
                return await self._call_with_retry(kwargs, force_json)
            except (openai.BadRequestError, ValueError) as e:
                # JSONDecodeError 是 ValueError 的子类，优先判断
                if isinstance(e, json.JSONDecodeError):
                    content_preview = str(e)[:200]
                    raise AIProviderUnavailableError(f"返回结果非合法JSON: {content_preview}")
                # 400：可能是 response_format 不支持，降级重试
                if "response_format" in kwargs:
                    kwargs.pop("response_format", None)
                    use_response_format = False
                    logger.info("response_format not supported, retrying without it")
                    await asyncio.sleep(1)
                    last_error = e
                    continue
                # 推理模型 reasoning 耗尽不可重试
                if isinstance(e, ValueError) and "推理模型 reasoning 耗尽" in str(e):
                    raise AIProviderUnavailableError(f"模型返回内容无效: {str(e)}")
                raise AIProviderUnavailableError(
                    f"请求参数错误: {str(e)[:200]}"
                    if isinstance(e, openai.BadRequestError)
                    else f"模型返回内容无效: {str(e)}"
                )
            except Exception as e:
                raise AIProviderUnavailableError(f"调用失败: {str(e)[:200]}")

        raise AIProviderUnavailableError(f"所有重试均失败: {last_error}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((
            openai.APITimeoutError,
            openai.RateLimitError,
            openai.APIStatusError,
            openai.APIError,
            json.JSONDecodeError,
        )),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def _call_with_retry(self, kwargs: dict, force_json: bool) -> dict:
        """实际的 API 调用，由 tenacity 管理网络级重试。"""
        resp = await self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        message = choice.message
        content = getattr(message, "content", None) or ""
        finish_reason = choice.finish_reason
        # 推理模型的 reasoning_content 是非标准扩展字段，用 getattr 安全取
        reasoning = getattr(message, "reasoning_content", "") or ""
        usage = getattr(resp, "usage", None)
        tokens = getattr(usage, "total_tokens", 0) if usage else 0

        # 检查内容是否为空
        if not content or not content.strip():
            # 推理模型 reasoning 耗尽 max_tokens：content 永远为空，重试无意义
            if finish_reason == "length" and reasoning:
                raise ValueError(
                    f"推理模型 reasoning 耗尽 max_tokens={self.max_tokens}"
                    f"（reasoning 长度 {len(reasoning)}），"
                    f"请增大 ai_provider.max_tokens 或改用非推理模型"
                )
            # 空内容由外层处理 response_format 降级
            raise ValueError("模型返回空内容")

        if force_json:
            parsed = _extract_json(content)
        else:
            parsed = content

        return {"content": parsed, "tokens": tokens, "model": self.model, "provider": self.base_url}
