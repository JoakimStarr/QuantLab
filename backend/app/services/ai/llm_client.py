import json
import re
import asyncio
from openai import AsyncOpenAI
import openai
from app.core.errors import AIProviderUnavailableError, AINotConfiguredError


def _extract_json(text: str):
    """从 LLM 返回文本中提取 JSON，处理 markdown 代码块包裹等情况。"""
    # 直接尝试解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 去掉 markdown 代码块: ```json\n...\n``` 或 ```\n...\n```
    code_block = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if code_block:
        try:
            return json.loads(code_block.group(1).strip())
        except json.JSONDecodeError:
            pass
    # 尝试提取第一个 JSON 数组或对象
    for pattern in [r"\[.*\]", r"\{.*\}"]:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    raise json.JSONDecodeError("无法从返回文本中提取 JSON", text, 0)


class LLMClient:
    def __init__(self, api_key: str, base_url: str, model: str, timeout: int = 15, max_tokens: int = 512, temperature: float = 0.3):
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
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=self.base_url,
            timeout=timeout,
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
        content = None  # 预初始化，防止 JSONDecodeError 处理时未定义
        for attempt in range(3):
            try:
                # 第二次尝试若之前可能因 response_format 失败，去掉重试
                if attempt > 0 and use_response_format and "response_format" in kwargs:
                    kwargs.pop("response_format", None)
                    use_response_format = False

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
                    if attempt == 0 and "response_format" in kwargs:
                        # 第一次返回空内容，可能是不支持 response_format，下次尝试不使用
                        kwargs.pop("response_format", None)
                        use_response_format = False
                        await asyncio.sleep(1)
                        continue
                    raise ValueError("模型返回空内容")

                if force_json:
                    parsed = _extract_json(content)
                else:
                    parsed = content
                return {"content": parsed, "tokens": tokens, "model": self.model, "provider": self.base_url}
            except openai.APITimeoutError:
                last_error = AIProviderUnavailableError(f"请求超时 (timeout={self.timeout}s)")
            except openai.RateLimitError:
                wait = (attempt + 1) * 3
                await asyncio.sleep(wait)
                last_error = AIProviderUnavailableError(f"API 速率限制 (429)，已等待 {wait}s 重试")
                continue
            except openai.BadRequestError as e:
                # 400：可能是 response_format 不支持，降级重试
                if "response_format" in kwargs:
                    kwargs.pop("response_format", None)
                    use_response_format = False
                    await asyncio.sleep(1)
                    last_error = AIProviderUnavailableError(f"请求参数错误(降级 response_format): {str(e)[:200]}")
                    continue
                last_error = AIProviderUnavailableError(f"请求参数错误: {str(e)[:200]}")
            except json.JSONDecodeError as e:
                content_preview = content[:200] if content else "空内容"
                last_error = AIProviderUnavailableError(f"返回结果非合法JSON: {content_preview}")
            except ValueError as e:
                last_error = AIProviderUnavailableError(f"模型返回内容无效: {str(e)}")
                # 推理模型 token 不足不可重试（重试结果相同），直接跳出让 router 切换 provider
                if "推理模型 reasoning 耗尽" in str(e):
                    break
            except openai.APIStatusError as e:
                last_error = AIProviderUnavailableError(f"调用失败: HTTP {e.status_code} {str(e)[:200]}")
            except openai.APIError as e:
                last_error = AIProviderUnavailableError(f"调用失败: {str(e)[:200]}")
            except Exception as e:
                last_error = AIProviderUnavailableError(f"调用失败: {str(e)[:200]}")
            if attempt < 2:
                await asyncio.sleep(2)  # 增加重试间隔，给 API 更多恢复时间
        raise last_error
