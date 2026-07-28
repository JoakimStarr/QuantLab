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
        if force_json:
            payload["response_format"] = {"type": "json_object"}
        last_error = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    if force_json:
                        parsed = _extract_json(content)
                    else:
                        parsed = content
                    return {"content": parsed, "tokens": data.get("usage", {}).get("total_tokens", 0), "model": self.model, "provider": self.base_url}
            except httpx.TimeoutException:
                last_error = AIProviderUnavailableError(f"请求超时 (timeout={self.timeout}s)")
            except json.JSONDecodeError:
                last_error = AIProviderUnavailableError(f"返回结果非合法JSON: {content[:200] if 'content' in dir() else '解析失败'}")
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
                await asyncio.sleep(1)
        raise last_error
