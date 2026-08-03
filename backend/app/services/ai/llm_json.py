"""LLM JSON 调用工具：统一 provider 路由 + 容错 JSON 解析。

消除各业务模块重复的「route_request + json.loads + json_repair」逻辑。
"""
import json
import logging

logger = logging.getLogger(__name__)


async def call_llm_json(messages: list) -> dict:
    """调用 LLM 并确保返回 dict。

    使用 ProviderRouter 的多提供商 failover，输出为 str 时先标准 JSON 解析，
    失败再用 json_repair 容错修复。
    """
    from app.services.ai.provider_router import ProviderRouter

    router = ProviderRouter()
    result = await router.route_request(messages)
    content = result["content"]
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            try:
                import json_repair
                content = json_repair.loads(content)
            except Exception as e:
                raise ValueError(f"LLM 返回无法解析为 JSON: {e}") from e
    if not isinstance(content, dict):
        raise ValueError(f"LLM 返回格式异常: {type(content)}")
    return content
