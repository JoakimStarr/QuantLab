"""AI 因子解释：为因子表达式生成金融逻辑描述。

用于挖掘入库时自动生成 description，以及因子库中缺失描述的因子补写。
复用 app.services.ai.llm_json.call_llm_json 调用 LLM。
"""
import logging

logger = logging.getLogger(__name__)


def _build_explain_prompt(expr: str, name: str = None) -> list[dict]:
    user_prompt = f"""你是量化因子分析师。请解释以下量化因子表达式的金融逻辑。

因子名: {name or '(未命名)'}
表达式: {expr}

请用通俗但专业的语言解释:
1. 这个因子在衡量什么（构造思路）
2. 它捕捉什么样的市场现象/投资者行为
3. 潜在的有效性逻辑（为什么可能预测收益）
4. 使用注意事项（如对极端值敏感、行业暴露等）

【输出格式】严格返回 JSON:
{{
  "summary": "一句话概括（30字内）",
  "logic": "因子构造逻辑与衡量对象（2-3句话）",
  "rationale": "为什么可能有效（2-3句话）",
  "caveats": ["注意事项1", "注意事项2"]
}}"""
    return [
        {"role": "system", "content": "你是一个严谨的量化因子分析师，输出必须是合法 JSON。"},
        {"role": "user", "content": user_prompt},
    ]


async def _call_llm(expr: str, name: str = None) -> dict:
    from app.services.ai.llm_json import call_llm_json

    return await call_llm_json(_build_explain_prompt(expr, name))


async def explain_factor(expression: str, name: str = None) -> dict:
    """为单个因子表达式生成 AI 解释。"""
    return await _call_llm(expression, name)


async def explain_and_update_factor(factor_id: int) -> dict:
    """为因子生成 AI 解释并写回 description 字段。

    Returns:
        {"factor_id", "description", "explanation"}
    """
    from sqlalchemy import select
    from app.core.database import async_session
    from app.models.factor import Factor

    async with async_session() as session:
        r = await session.execute(select(Factor).where(Factor.id == factor_id))
        factor = r.scalar_one_or_none()
        if factor is None:
            raise ValueError(f"因子不存在: {factor_id}")

    explanation = await _call_llm(factor.expression, factor.name)
    summary = explanation.get("summary") or ""
    logic = explanation.get("logic") or ""
    rationale = explanation.get("rationale") or ""
    description = f"[AI解释] {summary}\n{logic}\n{rationale}"[:500]

    async with async_session() as session:
        r = await session.execute(select(Factor).where(Factor.id == factor_id))
        factor = r.scalar_one_or_none()
        if factor:
            factor.description = description
            await session.commit()

    return {"factor_id": factor_id, "description": description, "explanation": explanation}


async def explain_factors_batch(factor_ids: list[int]) -> list[dict]:
    """批量解释因子（逐个调用 LLM，失败跳过）。"""
    results = []
    for fid in factor_ids:
        try:
            r = await explain_and_update_factor(fid)
            results.append(r)
        except Exception as e:
            logger.warning("因子 %s 解释失败: %s", fid, e)
    return results
