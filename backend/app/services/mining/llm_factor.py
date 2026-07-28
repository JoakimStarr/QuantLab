"""① LLM 生成因子：调用大模型产出 qlib 因子表达式，沙箱校验 + IC 评价后入库。

复用现有 ai/llm_client + ai/provider_router，CPU 密集的 IC 评价放线程池。
"""
import json
import logging
import asyncio
from datetime import datetime
from sqlalchemy import select
from app.core.database import async_session
from app.core.config import settings
from app.models.mining_task import MiningTask
from app.models.factor import Factor
from app.services.factor.expression import validate_expression, ExpressionValidationError
from app.services.factor.library import add_factor

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """你是一位资深量化研究员，擅长构造A股截面选股因子。
请基于 qlib 表达式语法生成有预测力的因子。"""

_USER_PROMPT_TEMPLATE = """请生成 {n} 个有 alpha 的 qlib 因子表达式，用于预测未来5日股票收益。

【可用算子】{ops}
【可用字段】{fields}
【语法示例】
- 动量: $close / Ref($close, 20) - 1   （注意：Ref 正数=过去，负数=未来，因子只能用过去数据）
- 波动: Std($close / Ref($close, 1) - 1, 20)
- 量价: Mean($volume, 5) / Mean($volume, 20)

【要求】
1. 每个因子给出 name(英文蛇形)、expression(合法qlib表达式)、description(中文简述)
2. 因子应有经济学含义，避免过拟合
3. 只能使用上述算子与字段，禁止 import/exec 等
4. 严禁使用负数 Ref（如 Ref($close, -5)）——那是未来数据，会造成 look-ahead bias

请严格返回 JSON 对象（不要返回数组），不要任何额外文字：
{{"factors": [{{"name": "momentum_20", "expression": "$close / Ref($close, 20) - 1", "description": "20日动量"}}]}}
"""


async def _call_llm(messages: list) -> list[dict]:
    """调用 LLM 生成候选因子，返回解析后的列表。"""
    from app.services.ai.provider_router import ProviderRouter
    router = ProviderRouter()
    result = await router.route_request(messages)
    content = result["content"]
    # content 可能是 dict 或 list 或 str
    if isinstance(content, str):
        content = json.loads(content)
    if isinstance(content, dict):
        # 兼容 {"factors": [...]} 形式
        content = content.get("factors") or content.get("data") or [content]
    if not isinstance(content, list):
        raise ValueError(f"LLM 返回格式异常: {type(content)}")
    return content


async def mine_with_llm(task_id: int, n_candidates: int = None) -> dict:
    """LLM 因子挖掘主流程。

    Args:
        task_id: MiningTask.id
        n_candidates: 候选因子数量
    Returns:
        统计 dict
    """
    mining_cfg = settings.mining.get("llm", {})
    n_candidates = n_candidates or mining_cfg.get("candidates_per_run", 10)
    ic_threshold = mining_cfg.get("ic_threshold", 0.03)
    allowed_ops = mining_cfg.get("allowed_ops", [])
    fields = ["$open", "$close", "$high", "$low", "$volume", "$amount", "$factor"]

    # 标记运行中
    await _update_task(task_id, status="running", started_at=datetime.now())

    try:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _USER_PROMPT_TEMPLATE.format(
                n=n_candidates, ops=", ".join(allowed_ops), fields=", ".join(fields)
            )},
        ]
        candidates = await _call_llm(messages)
        await _update_task(task_id, candidates_generated=len(candidates))

        passed_ids = []
        best_ic = 0.0
        for c in candidates:
            name = c.get("name", "").strip()
            expr = c.get("expression", "").strip()
            desc = c.get("description", "")
            if not name or not expr:
                continue
            # 沙箱校验
            try:
                validate_expression(expr)
            except ExpressionValidationError as e:
                logger.info("因子 %s 沙箱拒绝: %s", name, e)
                continue
            # IC 评价（CPU 密集，放线程池）
            try:
                ic_metrics = await _evaluate_safe(expr)
            except Exception as e:
                logger.warning("因子 %s 评价失败: %s", name, e)
                continue
            ic = ic_metrics.get("ic")
            if ic is None or abs(ic) < ic_threshold:
                logger.info("因子 %s IC=%s 未达标(阈值%s)", name, ic, ic_threshold)
                continue
            # 入库
            factor = await add_factor(name=name, expression=expr, category="llm",
                                      description=desc, source_task_id=task_id,
                                      skip_validation=True)
            # 更新评价指标
            await _save_metrics(factor["id"], ic_metrics)
            passed_ids.append(factor["id"])
            if ic is not None and abs(ic) > abs(best_ic):
                best_ic = ic

        await _update_task(
            task_id, status="done", candidates_passed=len(passed_ids),
            best_ic=best_ic, result_factor_ids=json.dumps(passed_ids),
            finished_at=datetime.now(),
        )
        return {"task_id": task_id, "generated": len(candidates),
                "passed": len(passed_ids), "best_ic": best_ic, "factor_ids": passed_ids}
    except Exception as e:
        await _update_task(task_id, status="failed", error=str(e)[:500],
                           finished_at=datetime.now())
        raise


async def _evaluate_safe(expr: str) -> dict:
    """在线程池中运行同步的因子评价。"""
    from app.services.quant.factor_eval import evaluate_factor
    period = settings.quant.get("default_backtest_period", {})
    start = period.get("start", "2020-01-01")
    end = period.get("end", "2024-12-31")
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, evaluate_factor, expr, start, end)


async def _save_metrics(factor_id: int, metrics: dict) -> None:
    from app.services.factor.library import update_factor_metrics
    await update_factor_metrics(factor_id, metrics)


async def _update_task(task_id: int, **kwargs):
    from app.services.mining.task_utils import update_task_status
    await update_task_status(task_id, **kwargs)
