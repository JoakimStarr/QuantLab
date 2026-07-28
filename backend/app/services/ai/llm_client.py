import json
import httpx
from app.core.errors import AIProviderUnavailableError, AINotConfiguredError


class LLMClient:
    def __init__(self, api_key: str, base_url: str, model: str, timeout: int = 8, max_tokens: int = 512, temperature: float = 0.3):
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
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    if force_json:
                        parsed = json.loads(content)
                    else:
                        parsed = content
                    return {"content": parsed, "tokens": data.get("usage", {}).get("total_tokens", 0), "model": self.model, "provider": self.base_url}
            except httpx.TimeoutException:
                last_error = AIProviderUnavailableError(f"请求超时 (timeout={self.timeout}s)")
            except json.JSONDecodeError:
                last_error = AIProviderUnavailableError("返回结果非合法JSON")
            except Exception as e:
                last_error = AIProviderUnavailableError(f"调用失败: {e}")
        raise last_error