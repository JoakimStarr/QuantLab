"""LLM 生成因子：调用大模型产出 qlib 因子表达式，沙箱校验 + IC 评价后入库。

增强：
- 并行 IC 评价：所有候选因子一次性提交到进程池，asyncio.gather 并行等待
- 多维验证：样本分割 + 滚动 IC + 统计显著性 + 多样性检测
- valid_ic 作为主筛选指标（替代全样本 IC）
- IC 结果缓存（LRU 上限 1024）+ GPU 检测
- 批量入库：通过评价的因子用 add_factors_batch 单次 commit
- iterative_mine_factors 迭代因子挖掘 —— 每轮生成→校验→IC评价→反馈给 LLM
"""
import json
import logging
import asyncio
import hashlib
from datetime import datetime
from cachetools import LRUCache
from app.core.config import settings
from app.core.gpu_utils import is_gpu_available
from app.services.factor.expression import validate_expression, ExpressionValidationError
from app.services.factor.library import add_factor, add_factors_batch, update_factor_metrics
from app.services.mining.task_utils import update_task_status as _update_task

logger = logging.getLogger(__name__)

# 启动时检测 GPU
_HAS_GPU = is_gpu_available()

# 本地 IC 缓存（LRU，上限保护）
_IC_CACHE: LRUCache = LRUCache(maxsize=1024)

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
    """LLM 因子挖掘主流程（并行IC评价 + 批量入库）。

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

        # Phase 1: 沙箱校验（主线程，快）
        valid = []
        for c in candidates:
            name = (c.get("name") or "").strip()
            expr = (c.get("expression") or "").strip()
            desc = c.get("description", "")
            if not name or not expr:
                continue
            try:
                validate_expression(expr)
            except ExpressionValidationError as e:
                logger.info("因子 %s 沙箱拒绝: %s", name, e)
                continue
            valid.append({"name": name, "expression": expr, "description": desc})

        if not valid:
            await _update_task(task_id, status="done", candidates_passed=0,
                               best_ic=0.0, finished_at=datetime.now())
            return {"task_id": task_id, "generated": len(candidates),
                    "passed": 0, "best_ic": 0.0, "factor_ids": []}

        # Phase 2: 并行多维验证（进程池，asyncio.gather 并行等待）
        logger.info("并行评价 %d 个候选因子（多维验证: 样本分割+统计显著性+滚动IC）", len(valid))
        eval_results = await asyncio.gather(
            *[_evaluate_with_validation(v["expression"]) for v in valid],
            return_exceptions=True,
        )

        # Phase 3: 筛选通过验证的因子（使用 valid_ic 作为主筛选指标）
        passed = []  # [(candidate, metrics), ...]
        best_ic = 0.0
        for v, result in zip(valid, eval_results):
            if isinstance(result, Exception):
                logger.warning("因子 %s 评价失败: %s", v["name"], result)
                continue
            # 使用 valid_ic 作为主筛选指标
            valid_ic = result.get("valid_ic")
            if result.get("passed") and valid_ic is not None and abs(valid_ic) >= ic_threshold:
                passed.append((v, result))
                if abs(valid_ic) > abs(best_ic):
                    best_ic = valid_ic
            else:
                reasons = result.get("fail_reasons", [])
                logger.info("因子 %s 未通过验证: valid_ic=%s, 原因: %s",
                           v["name"], valid_ic, "; ".join(reasons[:3]))
                # 兜底：如果 valid_ic 不可用，回退到全样本 IC
                if not any("valid_ic" in r for r in reasons):
                    ic = result.get("ic")
                    if ic is not None and abs(ic) >= ic_threshold:
                        logger.info("因子 %s 全样本 IC=%s 达标，作为后备", v["name"], ic)
                        passed.append((v, result))
                        if abs(ic) > abs(best_ic):
                            best_ic = ic

        # Phase 4: 批量入库 + 逐个保存指标（指标更新轻量，逐个可接受）
        passed_ids = []
        if passed:
            factor_dicts = [
                {"name": v["name"], "expression": v["expression"],
                 "category": "llm", "description": v.get("description", ""),
                 "source_task_id": task_id}
                for v, _ in passed
            ]
            factors = await add_factors_batch(factor_dicts, skip_validation=True)
            for factor, (_, metrics) in zip(factors, passed):
                await update_factor_metrics(factor["id"], metrics)
                passed_ids.append(factor["id"])

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


# ---------------- 迭代因子挖掘 ----------------

def _build_generation_prompt(template: dict, n_candidates: int) -> str:
    """构建首轮生成 prompt（结合模板与默认模板）。"""
    mining_cfg = settings.mining.get("llm", {})
    allowed_ops = template.get("allowed_ops") or mining_cfg.get("allowed_ops", [])
    fields = template.get("base_features") or ["$open", "$close", "$high", "$low", "$volume", "$amount", "$factor"]
    base_prompt = template.get("prompt") or template.get("llm_prompt") or _USER_PROMPT_TEMPLATE
    # 若模板自带 prompt，则在其后追加数量/约束说明
    if template.get("prompt") or template.get("llm_prompt"):
        return (
            base_prompt
            + f"\n\n请生成 {n_candidates} 个 qlib 因子表达式。\n"
            + f"【可用算子】{', '.join(allowed_ops)}\n"
            + f"【可用字段】{', '.join(fields)}\n"
            + "严禁使用负数 Ref（未来数据）。返回 JSON: {\"factors\":[{\"name\",\"expression\",\"description\"}]}"
        )
    return base_prompt.format(n=n_candidates, ops=", ".join(allowed_ops), fields=", ".join(fields))


def _build_feedback_prompt(template: dict, n_candidates: int,
                           prev_expressions: list, prev_round: dict) -> str:
    """构建反馈 prompt：把上一轮结果反馈给 LLM 以引导改进。"""
    feedback_lines = ["上一轮生成的因子及评价结果："]
    for r in prev_round.get("results", [])[:5]:
        feedback_lines.append(
            f"  表达式: {r['expression']}\n"
            f"  IC: {r.get('ic', 0):.4f}, RankIC: {r.get('rank_ic', 0) or 0:.4f}"
        )
    feedback_lines.append(f"\n上一轮最佳IC: {prev_round.get('best_ic', 0):.4f}")
    feedback_lines.append("\n请基于以上反馈，生成新的因子表达式。")
    feedback_lines.append("要求：")
    feedback_lines.append("1. 尝试不同的算子组合或参数")
    feedback_lines.append("2. 关注IC较高的因子的特征，尝试类似变体")
    feedback_lines.append("3. 避免与已有表达式重复")
    feedback_lines.append(f"4. 生成 {n_candidates} 个新的 qlib 表达式")

    base = _build_generation_prompt(template, n_candidates)
    return base + "\n\n" + "\n".join(feedback_lines)


def _is_duplicate(expr: str, existing_exprs: list, threshold: float = 0.9) -> bool:
    """简单的表达式去重（字符串相似度，difflib.SequenceMatcher）。"""
    from difflib import SequenceMatcher
    for existing in existing_exprs:
        ratio = SequenceMatcher(None, expr, existing).ratio()
        if ratio > threshold:
            return True
    return False


async def iterative_mine_factors(
    template: dict,
    n_rounds: int = 3,
    candidates_per_round: int = 5,
    task_id: int = None,
    **kwargs,
) -> dict:
    """LLM 迭代因子挖掘

    每轮：生成 -> 校验 -> IC评价 -> 反馈给LLM

    Args:
        template: 挖掘模板（包含 prompt/llm_prompt, base_features, allowed_ops 等）
        n_rounds: 迭代轮数
        candidates_per_round: 每轮生成的候选因子数
        task_id: 关联的挖掘任务 id（提供则更新任务状态/进度）

    Returns:
        {
            "rounds": [round_results],
            "best_factors": [factor_dicts],
            "improvement_curve": [ic_per_round],
            "n_rounds": n_rounds,
        }
    """
    mining_cfg = settings.mining.get("llm", {})
    ic_threshold = template.get("ic_threshold") or mining_cfg.get("ic_threshold", 0.03)

    all_best = []
    rounds_history = []
    improvement_curve = []
    prev_expressions = []
    generated_total = 0
    persisted_ids = []

    if task_id is not None:
        await _update_task(task_id, status="running", started_at=datetime.now())

    try:
        for round_idx in range(n_rounds):
            logger.info("LLM 迭代挖掘 - 第 %d 轮", round_idx + 1)

            # 构建 prompt（第 1 轮用原始模板，后续轮加入反馈）
            if round_idx == 0 or not rounds_history:
                prompt = _build_generation_prompt(template, candidates_per_round)
            else:
                prompt = _build_feedback_prompt(
                    template, candidates_per_round,
                    prev_expressions, rounds_history[-1],
                )

            # 调用 LLM 生成（messages 形式）
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
            candidates = await _call_llm(messages)
            generated_total += len(candidates)

            # 沙箱校验 + 去重
            valid_exprs = []
            for c in candidates:
                expr = (c.get("expression") or "").strip()
                name = (c.get("name") or "").strip()
                if not expr or not name:
                    continue
                try:
                    validate_expression(expr)
                except ExpressionValidationError as e:
                    logger.info("迭代因子 %s 沙箱拒绝: %s", name, e)
                    continue
                if _is_duplicate(expr, [e for e in prev_expressions] + [v["expression"] for v in valid_exprs] + [b["expression"] for b in all_best]):
                    continue
                valid_exprs.append({"name": name, "expression": expr, "description": c.get("description", "")})

            # IC 评价（并行：所有有效因子一次性提交到进程池，使用多维验证）
            round_results = []
            best_ic = 0.0
            if valid_exprs:
                eval_results = await asyncio.gather(
                    *[_evaluate_safe_cached(v["expression"]) for v in valid_exprs],
                    return_exceptions=True,
                )
                for v, ic_result in zip(valid_exprs, eval_results):
                    if isinstance(ic_result, Exception):
                        logger.warning("因子评价失败: %s, expr=%s", ic_result, v["expression"])
                        continue
                    # 使用 valid_ic 作为主筛选指标，回退到全样本 IC
                    valid_ic = ic_result.get("valid_ic")
                    if ic_result.get("passed") and valid_ic is not None and abs(valid_ic) >= ic_threshold:
                        pass
                    else:
                        valid_ic = ic_result.get("ic") or 0.0
                        if abs(valid_ic) < ic_threshold:
                            continue
                        # 未通过验证但全样本 IC 达标，作为后备
                        logger.info("因子 %s 未通过多维验证，全样本 IC=%s 达标作为后备",
                                    v["name"], valid_ic)
                    round_results.append({
                        "name": v["name"],
                        "expression": v["expression"],
                        "description": v.get("description", ""),
                        "ic": valid_ic,
                        "rank_ic": ic_result.get("rank_ic") or 0.0,
                        "icir": ic_result.get("icir") or 0.0,
                        "valid_ic": ic_result.get("valid_ic"),
                        "passed": ic_result.get("passed", False),
                    })
                    if abs(valid_ic) > abs(best_ic):
                        best_ic = valid_ic

            # 按 IC 排序，保留 top
            round_results.sort(key=lambda x: abs(x.get("ic", 0)), reverse=True)
            top_results = round_results[:3]
            # 即时入库，避免超时丢失已完成轮次的成果
            for f in top_results:
                try:
                    factor = await add_factor(name=f["name"], expression=f["expression"],
                                              category="llm", description=f.get("description", ""),
                                              source_task_id=task_id, skip_validation=True)
                    await update_factor_metrics(factor["id"], {
                        "ic": f.get("ic"), "rank_ic": f.get("rank_ic"),
                        "icir": f.get("icir"),
                    })
                    f["factor_id"] = factor["id"]
                    persisted_ids.append(factor["id"])
                except Exception as e:
                    logger.warning("迭代因子入库失败 %s: %s", f["name"], e)
            all_best.extend(top_results)

            if top_results:
                prev_expressions = [r["expression"] for r in top_results]

            rounds_history.append({
                "round": round_idx + 1,
                "generated": len(candidates),
                "valid": len(valid_exprs),
                "evaluated": len(round_results),
                "best_ic": best_ic,
                "results": round_results,
            })
            improvement_curve.append(best_ic)

            if task_id is not None:
                await _update_task(task_id, candidates_generated=generated_total,
                                   candidates_passed=len(persisted_ids), best_ic=best_ic)

            logger.info("第 %d 轮完成: 生成 %d, 有效 %d, 最佳IC=%.4f",
                        round_idx + 1, len(candidates), len(valid_exprs), best_ic)

        # 汇总最优因子（已在每轮即时入库，这里仅排序取最终结果）
        all_best.sort(key=lambda x: abs(x.get("ic", 0)), reverse=True)
        final_best = all_best[:10]

        best_ic_final = final_best[0]["ic"] if final_best else 0.0
        if task_id is not None:
            await _update_task(
                task_id, status="done", candidates_generated=generated_total,
                candidates_passed=len(persisted_ids), best_ic=best_ic_final,
                result_factor_ids=json.dumps(persisted_ids),
                finished_at=datetime.now(),
            )

        return {
            "rounds": rounds_history,
            "best_factors": final_best,
            "improvement_curve": improvement_curve,
            "n_rounds": n_rounds,
            "factor_ids": persisted_ids,
        }
    except Exception as e:
        if task_id is not None:
            await _update_task(task_id, status="failed", error=str(e)[:500],
                               finished_at=datetime.now())
        raise


async def mine_with_llm_iterative(task_id: int, n_rounds: int = 3,
                                  n_candidates: int = None) -> dict:
    """迭代挖掘任务包装器：构建默认模板并调用 iterative_mine_factors。"""
    mining_cfg = settings.mining.get("llm", {})
    n_candidates = n_candidates or mining_cfg.get("candidates_per_run", 5)
    template = {
        "prompt": "",
        "llm_prompt": _USER_PROMPT_TEMPLATE,
        "base_features": ["$open", "$close", "$high", "$low", "$volume", "$amount", "$factor"],
        "allowed_ops": mining_cfg.get("allowed_ops", []),
        "ic_threshold": mining_cfg.get("ic_threshold", 0.03),
    }
    return await iterative_mine_factors(
        template, n_rounds=n_rounds, candidates_per_round=n_candidates,
        task_id=task_id,
    )


async def _evaluate_with_validation(expr: str) -> dict:
    """在进程池中运行多维因子验证，带超时保护。

    使用 evaluate_factor_with_validation 替代旧的 evaluate_factor：
    - 样本分割：train/valid/test
    - 滚动 IC + 统计显著性
    - 使用 valid_ic 作为主筛选指标
    """
    from app.services.quant.factor_validator import evaluate_factor_with_validation
    period = settings.quant.get("default_backtest_period", {})
    start = period.get("start", "2020-01-01")
    end = period.get("end", "2024-12-31")
    horizon = settings.mining.get("llm", {}).get("eval_horizon", 5)
    timeout = settings.mining.get("llm", {}).get("eval_timeout_seconds", 120)
    from app.core.executor import run_cpu
    return await asyncio.wait_for(
        run_cpu(evaluate_factor_with_validation, expr, start, end, horizon=horizon),
        timeout=timeout,
    )


def _ic_cache_key(expr: str) -> str:
    """生成 IC 缓存 key：表达式 + 评价区间 + horizon。"""
    period = settings.quant.get("default_backtest_period", {})
    start = period.get("start", "2020-01-01")
    end = period.get("end", "2024-12-31")
    horizon = settings.mining.get("llm", {}).get("eval_horizon", 5)
    raw = f"{expr}|{start}|{end}|{horizon}"
    return hashlib.md5(raw.encode()).hexdigest()


def _ic_cache_put(key: str, value: dict) -> None:
    """写入 IC 缓存（LRUCache 自动淘汰最久未访问条目）。"""
    _IC_CACHE[key] = value


async def _evaluate_safe_cached(expr: str) -> dict:
    """带内存缓存的因子评价（使用 evaluate_factor_with_validation）。"""
    key = _ic_cache_key(expr)
    if key in _IC_CACHE:
        logger.debug("IC 缓存命中: %s", expr[:40])
        return _IC_CACHE[key]
    result = await _evaluate_with_validation(expr)
    _ic_cache_put(key, result)
    return result

