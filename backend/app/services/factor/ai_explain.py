"""AI 因子解释：为因子表达式生成金融逻辑描述，支持追问对话。

- 解释写回 factor.ai_explanation（完整结构化 JSON），factor.description 只存 summary 一句话简述
- 幂等：已有解释且未 force 时直接返回缓存，不再重复调用 LLM（同一因子一套介绍）
- 追问：chat_followup 把已有解释作为上下文，对话历史持久化到 factor.ai_chat_history
复用 app.services.ai.llm_json / provider_router 调用 LLM。
"""
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# 追问对话保留的最大消息条数（避免上下文无限膨胀）
_MAX_CHAT_HISTORY = 20


def _build_explain_prompt(expr: str, name: str = None) -> list[dict]:
    user_prompt = f"""你是资深量化因子分析师。请对以下量化因子表达式做一份详细的金融逻辑解读。

因子名: {name or '(未命名)'}
表达式: {expr}

请从以下维度详细展开（每个维度 3-4 句，专业且通俗）:
1. 因子构造逻辑：它具体怎么算的、衡量什么
2. 捕捉的市场现象与投资者行为
3. 有效性逻辑：为什么可能预测收益
4. 适用场景与注意事项（数据敏感性、风格/行业暴露、参数选择等）

【输出格式】严格返回 JSON:
{{
  "summary": "一句话概括（不超过 30 字）",
  "logic": "因子构造逻辑与衡量对象（详细展开）",
  "rationale": "为什么可能有效（详细展开）",
  "caveats": ["注意事项1", "注意事项2", "注意事项3"]
}}"""
    return [
        {"role": "system", "content": "你是一个严谨的量化因子分析师，输出必须是合法 JSON。"},
        {"role": "user", "content": user_prompt},
    ]


async def _call_llm(expr: str, name: str = None) -> dict:
    from app.services.ai.llm_json import call_llm_json

    return await call_llm_json(_build_explain_prompt(expr, name))


async def _chat_llm(messages: list) -> str:
    """自由文本追问调用（force_json=False，返回原始文本）。"""
    from app.services.ai.provider_router import ProviderRouter

    result = await ProviderRouter().route_request(messages, force_json=False)
    content = result.get("content")
    if isinstance(content, str):
        return content.strip()
    return json.dumps(content, ensure_ascii=False)


def _load_explanation(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        logger.warning("ai_explanation 解析失败，原始内容: %s...", str(raw)[:80])
        return None


def _load_chat_history(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _build_followup_messages(expr: str, name: str, explanation: dict, history: list[dict], question: str) -> list[dict]:
    context = (
        f"因子名: {name or '(未命名)'}\n"
        f"表达式: {expr}\n"
        f"已有解读-一句话概括: {explanation.get('summary', '')}\n"
        f"已有解读-构造逻辑: {explanation.get('logic', '')}\n"
        f"已有解读-有效性逻辑: {explanation.get('rationale', '')}\n"
        f"已有解读-注意事项: {'; '.join(explanation.get('caveats', []) or [])}"
    )
    messages = [
        {"role": "system", "content": (
            "你是量化因子分析师。请结合下方因子信息回答用户的追问，"
            "回答要专业、具体、通俗，如有需要可适当展开，避免空话。\n\n" + context
        )},
    ]
    # 追加历史对话（role 限定 user/assistant，防止注入异常角色）
    for m in history:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": question})
    return messages


async def explain_factor(expression: str, name: str = None) -> dict:
    """为单个因子表达式生成 AI 解释（不落库，供临时调用）。"""
    return await _call_llm(expression, name)


async def explain_and_update_factor(factor_id: int, force: bool = False) -> dict:
    """为因子生成 AI 解释并写回（幂等）。

    Returns:
        {"factor_id", "cached", "description", "explanation"}
    """
    from sqlalchemy import select
    from app.core.database import async_session
    from app.models.factor import Factor

    async with async_session() as session:
        r = await session.execute(select(Factor).where(Factor.id == factor_id))
        factor = r.scalar_one_or_none()
        if factor is None:
            raise ValueError(f"因子不存在: {factor_id}")

        existing = _load_explanation(factor.ai_explanation)
        if existing and not force:
            return {
                "factor_id": factor_id,
                "cached": True,
                "description": existing.get("summary") or factor.description,
                "explanation": existing,
            }

        factor_name = factor.name
        factor_expr = factor.expression

    explanation = await _call_llm(factor_expr, factor_name)
    summary = (explanation.get("summary") or "").strip()
    explanation["generated_at"] = datetime.now().isoformat()

    async with async_session() as session:
        r = await session.execute(select(Factor).where(Factor.id == factor_id))
        factor = r.scalar_one_or_none()
        if factor is None:
            raise ValueError(f"因子不存在: {factor_id}")
        factor.ai_explanation = json.dumps(explanation, ensure_ascii=False)
        factor.description = summary[:120] or None
        await session.commit()

    return {
        "factor_id": factor_id,
        "cached": False,
        "description": summary,
        "explanation": explanation,
    }


async def explain_factors_batch(factor_ids: list[int], force: bool = False) -> list[dict]:
    """批量解释因子（幂等：已有解释且非 force 时跳过，不重复调 LLM）。"""
    results = []
    for fid in factor_ids:
        try:
            r = await explain_and_update_factor(fid, force=force)
            results.append(r)
        except Exception as e:
            logger.warning("因子 %s 解释失败: %s", fid, e)
    return results


async def get_factor_ai_detail(factor_id: int) -> dict:
    """获取因子的完整 AI 解释与追问历史（供前端弹窗）。

    Returns:
        {"factor_id", "description", "explanation", "chat_history"}
    """
    from sqlalchemy import select
    from app.core.database import async_session
    from app.models.factor import Factor

    async with async_session() as session:
        r = await session.execute(select(Factor).where(Factor.id == factor_id))
        factor = r.scalar_one_or_none()
        if factor is None:
            raise ValueError(f"因子不存在: {factor_id}")
        return {
            "factor_id": factor_id,
            "name": factor.name,
            "expression": factor.expression,
            "description": factor.description,
            "explanation": _load_explanation(factor.ai_explanation),
            "chat_history": _load_chat_history(factor.ai_chat_history),
        }


async def chat_followup(factor_id: int, question: str) -> dict:
    """继续追问：把已有解释作为上下文，LLM 回答并持久化对话历史。

    若无已有解释，先自动生成一份再回答。

    Returns:
        {"factor_id", "answer", "chat_history"}
    """
    from sqlalchemy import select
    from app.core.database import async_session
    from app.models.factor import Factor

    if not question or not question.strip():
        raise ValueError("追问内容不能为空")

    async with async_session() as session:
        r = await session.execute(select(Factor).where(Factor.id == factor_id))
        factor = r.scalar_one_or_none()
        if factor is None:
            raise ValueError(f"因子不存在: {factor_id}")
        explanation = _load_explanation(factor.ai_explanation)
        history = _load_chat_history(factor.ai_chat_history)
        factor_name = factor.name
        factor_expr = factor.expression

    if not explanation:
        await explain_and_update_factor(factor_id)
        async with async_session() as session:
            r = await session.execute(select(Factor).where(Factor.id == factor_id))
            factor = r.scalar_one_or_none()
            explanation = _load_explanation(factor.ai_explanation) if factor else None
            history = _load_chat_history(factor.ai_chat_history) if factor else []

    if not explanation:
        raise ValueError("因子解释生成失败，请稍后重试")

    messages = _build_followup_messages(factor_expr, factor_name, explanation, history, question)
    answer = await _chat_llm(messages)

    now = datetime.now().isoformat()
    history = history + [
        {"role": "user", "content": question, "ts": now},
        {"role": "assistant", "content": answer, "ts": now},
    ]
    history = history[-_MAX_CHAT_HISTORY:]

    async with async_session() as session:
        r = await session.execute(select(Factor).where(Factor.id == factor_id))
        factor = r.scalar_one_or_none()
        if factor is not None:
            factor.ai_chat_history = json.dumps(history, ensure_ascii=False)
            await session.commit()

    return {"factor_id": factor_id, "answer": answer, "chat_history": history}
