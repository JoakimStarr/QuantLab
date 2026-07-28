import json
import re
import asyncio
import httpx
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
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature

    async def chat_completion(self, messages: list, force_json: bool = True) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {"model": self.model, "messages": messages, "temperature": self.temperature, "max_tokens": self.max_tokens}

        # 免费模型可能不支持response_format，先尝试带response_format
        use_response_format = force_json
        if use_response_format:
            payload["response_format"] = {"type": "json_object"}

        last_error = None
        content = None  # 预初始化，防止 JSONDecodeError 处理时未定义
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    # 如果是第二次尝试且之前返回空内容，尝试不使用response_format
                    if attempt > 0 and use_response_format:
                        payload.pop("response_format", None)
                        use_response_format = False

                    resp = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    choice = data["choices"][0]
                    content = choice.get("message", {}).get("content", "") or ""
                    finish_reason = choice.get("finish_reason")
                    reasoning = choice.get("message", {}).get("reasoning_content", "") or ""

                    # 检查内容是否为空
                    if not content or not content.strip():
                        # 推理模型 reasoning 耗尽 max_tokens：content 永远为空，重试无意义
                        if finish_reason == "length" and reasoning:
                            raise ValueError(
                                f"推理模型 reasoning 耗尽 max_tokens={self.max_tokens}"
                                f"（reasoning 长度 {len(reasoning)}），"
                                f"请增大 ai_provider.max_tokens 或改用非推理模型"
                            )
                        if attempt == 0 and "response_format" in payload:
                            # 第一次返回空内容，可能是不支持response_format，下次尝试不使用
                            payload.pop("response_format", None)
                            use_response_format = False
                            await asyncio.sleep(1)
                            continue
                        raise ValueError("模型返回空内容")

                    if force_json:
                        parsed = _extract_json(content)
                    else:
                        parsed = content
                    return {"content": parsed, "tokens": data.get("usage", {}).get("total_tokens", 0), "model": self.model, "provider": self.base_url}
            except httpx.TimeoutException:
                last_error = AIProviderUnavailableError(f"请求超时 (timeout={self.timeout}s)")
            except json.JSONDecodeError as e:
                content_preview = content[:200] if content else '空内容'
                last_error = AIProviderUnavailableError(f"返回结果非合法JSON: {content_preview}")
            except ValueError as e:
                last_error = AIProviderUnavailableError(f"模型返回内容无效: {str(e)}")
                # 推理模型 token 不足不可重试（重试结果相同），直接跳出让 router 切换 provider
                if "推理模型 reasoning 耗尽" in str(e):
                    break
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    wait = (attempt + 1) * 3
                    await asyncio.sleep(wait)
                    last_error = AIProviderUnavailableError(f"API 速率限制 (429)，已等待 {wait}s 重试")
                    continue
                last_error = AIProviderUnavailableError(f"调用失败: {e}")
            except Exception as e:
                last_error = AIProviderUnavailableError(f"调用失败: {e}")
            if attempt < 2:
                await asyncio.sleep(2)  # 增加重试间隔，给API更多恢复时间
        raise last_error
