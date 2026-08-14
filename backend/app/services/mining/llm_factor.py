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
from app.core.executor import run_cpu
from app.services.mining.task_utils import update_task_status as _update_task
from app.services.quant.factor_validator import bh_corrected_pvalues

logger = logging.getLogger(__name__)

# 启动时检测 GPU
_HAS_GPU = is_gpu_available()

# 本地 IC 缓存（LRU，上限保护）
_IC_CACHE: LRUCache = LRUCache(maxsize=1024)

# 已有因子 IC 序列缓存（web 进程侧，key=表达式集合签名 md5，因子库变化自动失效）。
# 注意：factor_validator 里的 _EXISTING_IC_CACHE 跑在进程池 worker 内无法跨调用共享，
# 统一在这里缓存才真正生效，避免每次挖掘重复计算已有因子的全量 IC 序列。
_EXISTING_IC_WEB_CACHE: LRUCache = LRUCache(maxsize=16)

# 评价并发信号量（懒初始化）：限制同时进入进程池的候选数 = cpu_workers，
# 避免大量候选一次性涌入小进程池排队，把单候选超时（eval_timeout_seconds）耗尽。
# 信号量在 wait_for 之外获取，排队等待不计入超时。
_EVAL_SEM: asyncio.Semaphore | None = None


def _get_eval_semaphore() -> asyncio.Semaphore:
    """获取候选评价并发信号量（worker 数 = cpu_workers）。"""
    global _EVAL_SEM
    if _EVAL_SEM is None:
        workers = max(1, int((settings.task or {}).get("cpu_workers", 4)))
        _EVAL_SEM = asyncio.Semaphore(workers)
        logger.debug("候选评价并发上限配置: %d", workers)
    return _EVAL_SEM


async def _evaluate_bounded(expr: str, existing_ic_series: list = None,
                            universe: str = None, cached: bool = False) -> dict:
    """带并发上限的候选评价。

    在信号量保护下调用评价，保证同一时间在途任务数不超过进程池 worker 数，
    进程池不会堆积长队列；超时（eval_timeout_seconds）因此只度量实际执行时间。
    cached=True 时走带内存缓存的评价路径（迭代挖掘用）。
    """
    async with _get_eval_semaphore():
        if cached:
            return await _evaluate_safe_cached(expr, existing_ic_series=existing_ic_series,
                                               universe=universe)
        return await _evaluate_with_validation(expr, existing_ic_series=existing_ic_series,
                                               universe=universe)

# 可用的基础字段（与 qlib bin 实际写入一致，含估值/换手/宏观/财报）
_AVAILABLE_FIELDS = [
    "$open", "$high", "$low", "$close", "$preclose",
    "$volume", "$amount", "$turn",
    "$tradestatus", "$pct_chg", "$is_st",
    "$pe_ttm", "$pb_mrq", "$ps_ttm", "$pcf_ncf_ttm",
    "$adjustflag", "$change", "$tradable",
    # 季频财报字段（fundamental_sync PIT 广播，财报数据拉取+补齐后可用）
    "$netprofit", "$revenue", "$netprofit_deduct", "$roe", "$roa",
    "$gross_margin", "$net_margin", "$debt_ratio", "$ocf",
    "$eps", "$bvps", "$revenue_yoy", "$netprofit_yoy", "$ocf_to_np",
    "$current_ratio", "$quick_ratio", "$equity_multiplier",
]

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
5. 严禁在表达式开头写一元负号（如 "-Mean(...)" 或 "-$close"）——qlib 不支持这种写法。
   需要取反时请写成 "X * -1"（如 "Mean($close, 5) * -1" 表示负的均线）

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


async def _load_existing_ic_series() -> list:
    """加载因子库中已达标（|IC|>=阈值）因子的 IC 序列，用于多样性检测。

    - 只取 llm/symbolic 挖掘因子（Alpha158 基准因子量大且相关性高，不参与去重）
    - 数量上限 diversity_max_factors（默认 20），带内存缓存，重复挖掘不重复计算
    """
    from sqlalchemy import select, func
    from app.core.database import async_session
    from app.models.factor import Factor
    from app.services.quant.factor_validator import compute_existing_ic_series

    mining_cfg = settings.mining.get("llm", {})
    threshold = mining_cfg.get("ic_threshold", 0.03)
    limit = mining_cfg.get("diversity_max_factors", 20)
    period = settings.quant.get("default_backtest_period", {})
    start = period.get("start", "2020-01-01")
    end = period.get("end", "2024-12-31")
    horizon = mining_cfg.get("eval_horizon", 5)

    async with async_session() as session:
        result = await session.execute(
            select(Factor.expression)
            .where(Factor.status == "active")
            .where(Factor.category.in_(["llm", "symbolic"]))
            .where(func.abs(Factor.ic) >= threshold)
            .limit(limit)
        )
        exprs = [r[0] for r in result.all()]
    if not exprs:
        logger.info("无已达标因子，本次跳过多样性检测")
        return []

    # 按表达式集合签名缓存（web 进程侧）：因子库新增/删除达标因子后自动 miss
    import hashlib
    sig = hashlib.md5("|".join(sorted(exprs)).encode()).hexdigest()
    cached = _EXISTING_IC_WEB_CACHE.get(sig)
    if cached is not None:
        return cached

    series_list = await run_cpu(compute_existing_ic_series, exprs, start, end, horizon=horizon)
    logger.info("多样性检测: 加载 %d 个已有因子 IC 序列", len(series_list))
    _EXISTING_IC_WEB_CACHE[sig] = series_list
    return series_list


async def mine_with_llm(task_id: int, n_candidates: int = None, universe: str = None) -> dict:
    """LLM 因子挖掘主流程（并行IC评价 + 批量入库）。

    Args:
        task_id: MiningTask.id
        n_candidates: 候选因子数量
        universe: 标的池（csi300/csi500/all/etf_all...），None=config 默认
    Returns:
        统计 dict
    """
    mining_cfg = settings.mining.get("llm", {})
    n_candidates = n_candidates or mining_cfg.get("candidates_per_run", 10)
    ic_threshold = mining_cfg.get("ic_threshold", 0.03)
    significance_alpha = mining_cfg.get("significance_alpha", 0.05)
    # BH 多重检验的 FDR 水平与显著性 alpha 解耦：批内候选多（如 50 个）时
    # p_adj 按 m 倍放大，用同一 alpha 会堵死产出；bh_alpha 单独控制假阳性率。
    bh_alpha = mining_cfg.get("bh_alpha", 0.20)
    allowed_ops = mining_cfg.get("allowed_ops", [])
    fields = _AVAILABLE_FIELDS

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
        # 候选即刻落库（status=generated）：LLM 已产出即可复盘，后续阶段幂等更新
        from app.services.mining.candidate_store import upsert_candidates
        await upsert_candidates(task_id, [
            {"name": c.get("name"), "expression": (c.get("expression") or "").strip(),
             "description": c.get("description", ""), "status": "generated"}
            for c in candidates
        ], round_no=1)

        # Phase 1: 沙箱校验（主线程，快）
        valid = []
        rejected = []
        for c in candidates:
            name = (c.get("name") or "").strip()
            expr = (c.get("expression") or "").strip()
            desc = c.get("description", "")
            if not name or not expr:
                rejected.append({"name": name, "expression": expr, "description": desc,
                                 "reason": "表达式或名称为空"})
                continue
            try:
                cleaned = validate_expression(expr)
            except ExpressionValidationError as e:
                logger.info("因子 %s 沙箱拒绝: %s", name, e)
                rejected.append({"name": name, "expression": expr, "description": desc,
                                 "reason": f"沙箱拒绝: {e}"})
                continue
            # 使用清洗后的表达式（含一元负号改写），避免 qlib unary minus 评价失败
            valid.append({"name": name, "expression": cleaned, "description": desc})
        if rejected:
            await upsert_candidates(task_id, [
                {**r, "status": "rejected"} for r in rejected
            ], round_no=1)

        if not valid:
            await _update_task(task_id, status="done", candidates_passed=0,
                               best_ic=0.0, finished_at=datetime.now())
            return {"task_id": task_id, "generated": len(candidates),
                    "passed": 0, "best_ic": 0.0, "factor_ids": []}

        # Phase 2: 并行多维验证（进程池，asyncio.gather 并行等待；并发上限 = cpu_workers）
        logger.info("并行评价 %d 个候选因子（多维验证: 样本分割+统计显著性+滚动IC）", len(valid))
        # 多样性检测：加载已有因子 IC 序列（缓存，仅需一次）
        existing_ic_series = await _load_existing_ic_series()
        eval_results = await asyncio.gather(
            *[_evaluate_bounded(v["expression"], existing_ic_series=existing_ic_series,
                                universe=universe, cached=True)
              for v in valid],
            return_exceptions=True,
        )

        # Phase 2.5: BH 多重检验校正（避免多次试验下的假阳性）
        p_vals = [
            None if isinstance(r, Exception) else (r.get("significance") or {}).get("p_value")
            for r in eval_results
        ]
        p_adj = bh_corrected_pvalues(p_vals)
        for i, pa in enumerate(p_adj):
            if isinstance(eval_results[i], Exception) or pa is None:
                continue
            r = eval_results[i]
            # 复制避免污染 IC 缓存对象（p_adj 依赖本批候选集合）
            eval_results[i] = {
                **r,
                "significance": {**(r.get("significance") or {}), "p_adj": pa},
                "p_adj": pa,
            }

        # Phase 3: 筛选通过验证的因子（使用 valid_ic 作为主筛选指标）
        passed = []  # [(candidate, metrics), ...]
        best_ic = 0.0
        evaluated = []  # 候选评价记录（落库用）
        for v, result in zip(valid, eval_results):
            if isinstance(result, Exception):
                logger.warning("因子 %s 评价失败: %s", v["name"], result)
                evaluated.append({"name": v["name"], "expression": v["expression"],
                                  "description": v.get("description", ""),
                                  "status": "rejected", "reason": f"评价异常: {str(result)[:200]}"})
                continue
            # BH 校正后显著性（无 p 值视为未通过多重检验保护，但保留兜底路径）
            sig = result.get("significance") or {}
            p_adj_val = sig.get("p_adj")
            bh_ok = p_adj_val is None or p_adj_val < bh_alpha
            if not bh_ok:
                logger.info("因子 %s 未通过 BH 多重检验校正: p_adj=%s", v["name"], p_adj_val)
            # 使用 valid_ic 作为主筛选指标
            valid_ic = result.get("valid_ic")
            if result.get("passed") and valid_ic is not None and abs(valid_ic) >= ic_threshold and bh_ok:
                passed.append((v, result))
                evaluated.append({"name": v["name"], "expression": v["expression"],
                                  "description": v.get("description", ""),
                                  "status": "passed", "ic": valid_ic,
                                  "rank_ic": result.get("rank_ic"),
                                  "icir": result.get("icir")})
                if abs(valid_ic) > abs(best_ic):
                    best_ic = valid_ic
            else:
                reasons = result.get("fail_reasons", [])
                logger.info("因子 %s 未通过验证: valid_ic=%s, 原因: %s",
                            v["name"], valid_ic, "; ".join(reasons[:3]))
                # 未通过给具体原因落库
                evaluated.append({"name": v["name"], "expression": v["expression"],
                                  "description": v.get("description", ""),
                                  "status": "rejected", "ic": valid_ic,
                                  "rank_ic": result.get("rank_ic"),
                                  "reason": f"valid_ic={valid_ic}；" + "; ".join(reasons[:3])})
                # 不做全样本 IC 兜底：多维验证未通过说明因子不稳健（稳定性/显著性/
                # 衰减/多样性不过关），全样本 IC 达标反而是过拟合信号，放行会污染因子库

        # Phase 3.5: 批内候选互查（同一批高度相关的因子只留 |IC| 最高者）
        if len(passed) > 1:
            diversity_threshold = settings.mining.get("llm", {}).get("diversity_threshold", 0.8)
            deduped = _dedupe_intra_batch(
                [{"name": v["name"], "valid_ic": r.get("valid_ic"),
                  "valid_ic_series": r.get("valid_ic_series")}
                 for v, r in passed],
                diversity_threshold=diversity_threshold,
            )
            deduped_names = {d["name"] for d in deduped}
            dropped = [(v["name"]) for v, _ in passed if v["name"] not in deduped_names]
            if dropped:
                logger.info("批内去重剔除 %d 个冗余候选: %s", len(dropped), dropped[:5])
            passed = [(v, r) for v, r in passed if v["name"] in deduped_names]

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

        # 批量落库候选评价结果（幂等更新；通过者补 factor 关联可后续查 factor_id）
        if evaluated:
            await upsert_candidates(task_id, evaluated, round_no=1)

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
    """构建首轮生成 prompt（结合模板与默认模板）。

    多样性增强：要求候选因子覆盖不同风格（时序/截面/量价/波动/估值），
    避免全部生成同一类型的动量变体。
    """
    mining_cfg = settings.mining.get("llm", {})
    allowed_ops = template.get("allowed_ops") or mining_cfg.get("allowed_ops", [])
    fields = template.get("base_features") or _AVAILABLE_FIELDS
    base_prompt = template.get("prompt") or template.get("llm_prompt") or _USER_PROMPT_TEMPLATE
    # 候选池参考（若有）：给 LLM 提供可参考的表达式，提高命中率
    candidate_hint = ""
    try:
        from app.services.mining.candidate_pool import format_candidates_for_prompt, get_candidates_for_template
        cands = get_candidates_for_template(template, n=6)
        if cands:
            candidate_hint = format_candidates_for_prompt(cands) + "\n"
    except Exception:
        candidate_hint = ""
    # 若模板自带 prompt，则在其后追加数量/约束说明
    if template.get("prompt") or template.get("llm_prompt"):
        return (
            base_prompt
            + f"\n\n请生成 {n_candidates} 个 qlib 因子表达式。\n"
            + f"【可用算子】{', '.join(allowed_ops)}\n"
            + f"【可用字段】{', '.join(fields)}\n"
            + (candidate_hint or "")
            + "【多样性要求】生成的因子应尽量覆盖不同风格："
            + "动量趋势、均值反转、波动率、量价关系、估值（$pe_ttm/$pb_mrq/$ps_ttm/$pcf_ncf_ttm 可用）、"
            + "换手（$turn 可用）。避免全部因子都是同一类型的变体。\n"
            + "严禁使用负数 Ref（未来数据）。返回 JSON: {\"factors\":[{\"name\",\"expression\",\"description\"}]}"
        )
    return base_prompt.format(n=n_candidates, ops=", ".join(allowed_ops), fields=", ".join(fields))


def _build_feedback_prompt(template: dict, n_candidates: int,
                           prev_expressions: list, prev_round: dict) -> str:
    """构建反馈 prompt：把上一轮结果反馈给 LLM 以引导改进。

    增强：
    - 展示上一轮被沙箱拒绝的表达式及原因，避免 LLM 重犯
    - 若上一轮全部未达标，明确要求改变策略（避免围绕高IC因子出变体）
    """
    feedback_lines = ["上一轮生成的因子及评价结果："]
    for r in prev_round.get("results", [])[:5]:
        feedback_lines.append(
            f"  表达式: {r['expression']}\n"
            f"  IC: {r.get('ic', 0):.4f}, RankIC: {r.get('rank_ic', 0) or 0:.4f}"
        )
    feedback_lines.append(f"\n上一轮最佳IC: {prev_round.get('best_ic', 0):.4f}")

    # 展示被拒表达式（强反馈：避免重犯）
    rejected = prev_round.get("rejected", [])
    if rejected:
        feedback_lines.append("\n上一轮被拒绝的表达式（请避免类似结构）：")
        for rej in rejected[:5]:
            feedback_lines.append(
                f"  表达式: {rej.get('expression', '')} 原因: {rej.get('reason', '')}"
            )

    # 全不达标时强反馈
    if prev_round.get("best_ic", 0) < 0.03:
        feedback_lines.append(
            "\n⚠️ 上一轮所有因子 IC 均未达标。请彻底改变策略："
            "更换算子组合、更换字段、改变窗口长度，不要重复上一轮的结构。"
        )

    feedback_lines.append("\n请基于以上反馈，生成新的因子表达式。")
    feedback_lines.append("要求：")
    feedback_lines.append("1. 尝试不同的算子组合或参数")
    feedback_lines.append("2. 关注IC较高的因子的特征，尝试类似变体")
    feedback_lines.append("3. 避免与已有表达式重复")
    feedback_lines.append(f"4. 生成 {n_candidates} 个新的 qlib 表达式")

    base = _build_generation_prompt(template, n_candidates)
    return base + "\n\n" + "\n".join(feedback_lines)


def _normalize_expr(expr: str) -> str:
    """归一化表达式：去空白、统一大小写（qlib 算子名不区分大小写时）。"""
    import re
    return re.sub(r"\s+", "", expr).lower()


def _is_duplicate(expr: str, existing_exprs: list) -> str | None:
    """表达式去重（归一化后完全一致才算重复），返回原因或 None。

    不用字符串相似度：相似 ≠ 等价——`Ref($close, 20)` 与 `Ref($close, 30)`
    文本相似度 >0.9 却是两个不同因子，会被误杀；而语义重复（等价变形）相似度
    可能很低，会漏检。语义级去重交给 IC 序列相关性（DiversityChecker /
    _dedupe_intra_batch），这里只拦真正的重复项，避免浪费评价计算。

    Args:
        expr: 候选表达式
        existing_exprs: 已有表达式列表

    Returns:
        None 表示不重复；否则返回原因字符串（如 "与已有表达式重复"）。
    """
    norm = _normalize_expr(expr)
    for existing in existing_exprs:
        if norm == _normalize_expr(existing):
            return "与已有表达式重复"
    return None


def _dedupe_intra_batch(candidates: list, diversity_threshold: float = 0.8):
    """批内候选互查（IC 序列相关性去重）。

    DiversityChecker 只对比因子库里的"已有因子"，同一批候选之间不会互查——
    LLM 一轮可能生成两个高度相关的因子同时入库。这里对通过筛选的候选两两比对
    valid_ic_series 相关性，|corr|>threshold 时保留 |IC| 更高者。

    Args:
        candidates: 已通过其余筛选的候选列表，元素 dict，须含
            "valid_ic_series"（list/Series）、"valid_ic"（float，用于择优）
        diversity_threshold: 相关阈值，超过即视为冗余

    Returns:
        去重后的候选列表（保留每个冗余组中 |valid_ic| 最高者）
    """
    import pandas as pd
    if len(candidates) < 2:
        return candidates
    keep = []
    for cand in candidates:
        series = cand.get("valid_ic_series")
        s_new = pd.Series(series if series is not None else []).dropna()
        if len(s_new) < 10:
            keep.append(cand)
            continue
        redundant = False
        for kept in keep:
            series_old = kept.get("valid_ic_series")
            s_old = pd.Series(series_old if series_old is not None else []).dropna()
            aligned = pd.concat([s_new, s_old], axis=1).dropna()
            if len(aligned) < 10:
                continue
            corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
            if not pd.isna(corr) and abs(corr) > diversity_threshold:
                # 保留 |IC| 更高的
                if abs(cand.get("valid_ic") or 0) > abs(kept.get("valid_ic") or 0):
                    keep.remove(kept)
                    keep.append(cand)
                redundant = True
                break
        if not redundant:
            keep.append(cand)
    return keep


async def iterative_mine_factors(
    template: dict,
    n_rounds: int = 3,
    candidates_per_round: int = 5,
    task_id: int = None,
    universe: str = None,
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
    significance_alpha = mining_cfg.get("significance_alpha", 0.05)
    bh_alpha = mining_cfg.get("bh_alpha", 0.20)

    all_best = []
    rounds_history = []
    improvement_curve = []
    prev_expressions = []
    generated_total = 0
    persisted_ids = []
    existing_ic_series = None  # 懒加载：仅当存在候选时才计算

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
            # 候选即刻落库（status=generated），后续阶段按状态幂等更新
            if task_id is not None:
                from app.services.mining.candidate_store import upsert_candidates
                await upsert_candidates(task_id, [
                    {"name": c.get("name"), "expression": (c.get("expression") or "").strip(),
                     "description": c.get("description", ""), "status": "generated"}
                    for c in candidates
                ], round_no=round_idx + 1)

            # 沙箱校验 + 去重
            valid_exprs = []
            rejected = []
            for c in candidates:
                expr = (c.get("expression") or "").strip()
                name = (c.get("name") or "").strip()
                if not expr or not name:
                    rejected.append({"expression": expr, "reason": "表达式或名称为空"})
                    continue
                try:
                    cleaned = validate_expression(expr)
                except ExpressionValidationError as e:
                    logger.info("迭代因子 %s 沙箱拒绝: %s", name, e)
                    rejected.append({"expression": expr, "reason": f"沙箱拒绝: {e}"})
                    continue
                known = [e for e in prev_expressions]
                known += [v["expression"] for v in valid_exprs]
                known += [b["expression"] for b in all_best]
                dup = _is_duplicate(expr, known)
                if dup:
                    rejected.append({"expression": expr, "reason": dup})
                    continue
                valid_exprs.append({"name": name, "expression": cleaned, "description": c.get("description", "")})

            # IC 评价（并行：所有有效因子一次性提交到进程池，使用多维验证）
            round_results = []
            best_ic = 0.0
            iter_evaluated = []  # 落库记录：通过者+未通过者（含原因）
            if valid_exprs:
                # 多样性检测序列懒加载一次（复用已有因子库）
                if existing_ic_series is None:
                    existing_ic_series = await _load_existing_ic_series()
                eval_results = await asyncio.gather(
                    *[_evaluate_bounded(v["expression"], existing_ic_series=existing_ic_series,
                                        universe=universe, cached=True)
                      for v in valid_exprs],
                    return_exceptions=True,
                )
                # BH 多重检验校正
                p_vals = [
                    None if isinstance(r, Exception) else (r.get("significance") or {}).get("p_value")
                    for r in eval_results
                ]
                p_adj = bh_corrected_pvalues(p_vals)
                for i, pa in enumerate(p_adj):
                    if isinstance(eval_results[i], Exception) or pa is None:
                        continue
                    r = eval_results[i]
                    eval_results[i] = {
                        **r,
                        "significance": {**(r.get("significance") or {}), "p_adj": pa},
                        "p_adj": pa,
                    }
                for v, ic_result in zip(valid_exprs, eval_results):
                    if isinstance(ic_result, Exception):
                        logger.warning("因子评价失败: %s, expr=%s", ic_result, v["expression"])
                        iter_evaluated.append({"name": v["name"], "expression": v["expression"],
                                               "description": v.get("description", ""),
                                               "status": "rejected",
                                               "reason": f"评价异常: {str(ic_result)[:200]}"})
                        continue
                    # BH 校正后显著性约束
                    sig = ic_result.get("significance") or {}
                    p_adj_val = sig.get("p_adj")
                    bh_ok = p_adj_val is None or p_adj_val < bh_alpha
                    # 使用 valid_ic 作为主筛选指标，回退到全样本 IC
                    valid_ic = ic_result.get("valid_ic")
                    passed_ok = (ic_result.get("passed") and valid_ic is not None
                                 and abs(valid_ic) >= ic_threshold)
                    if not passed_ok or not bh_ok:
                        if not bh_ok:
                            logger.info("因子 %s 未通过 BH 校正: p_adj=%s", v["name"], p_adj_val)
                            iter_evaluated.append({
                                "name": v["name"], "expression": v["expression"],
                                "description": v.get("description", ""),
                                "status": "rejected", "ic": ic_result.get("valid_ic"),
                                "reason": f"未通过 BH 多重检验校正 (p_adj={p_adj_val:.4f})"})
                        else:
                            reasons = (ic_result.get("fail_reasons") or [])
                            logger.info("因子 %s 未通过多维验证: valid_ic=%s, 原因: %s",
                                        v["name"], valid_ic, "; ".join(reasons[:3]))
                            iter_evaluated.append({
                                "name": v["name"], "expression": v["expression"],
                                "description": v.get("description", ""),
                                "status": "rejected", "ic": valid_ic,
                                "rank_ic": ic_result.get("rank_ic"),
                                "reason": f"valid_ic={valid_ic}；" + "; ".join(reasons[:3])})
                        # 不做全样本 IC 兜底（理由同单轮挖掘）：未过验证即不稳健
                        continue
                    round_results.append({
                        "name": v["name"],
                        "expression": v["expression"],
                        "description": v.get("description", ""),
                        "ic": valid_ic,
                        "rank_ic": ic_result.get("rank_ic") or 0.0,
                        "icir": ic_result.get("icir") or 0.0,
                        "valid_ic": ic_result.get("valid_ic"),
                        "valid_ic_series": ic_result.get("valid_ic_series"),
                        "passed": ic_result.get("passed", False),
                    })
                    if abs(valid_ic) > abs(best_ic):
                        best_ic = valid_ic

            # 批内候选互查：同一轮生成的高度相关因子只保留 |IC| 最高者
            if len(round_results) > 1:
                diversity_threshold = mining_cfg.get("diversity_threshold", 0.8)
                deduped = _dedupe_intra_batch(round_results, diversity_threshold=diversity_threshold)
                deduped_names = {d["name"] for d in deduped}
                dropped = [r["name"] for r in round_results if r["name"] not in deduped_names]
                if dropped:
                    logger.info("迭代第 %d 轮批内去重剔除 %d 个冗余候选: %s",
                                round_idx + 1, len(dropped), dropped[:5])
                round_results = [r for r in round_results if r["name"] in deduped_names]

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
                "rejected": rejected,
            })
            improvement_curve.append(best_ic)

            # 本轮候选落库：rejected（沙箱/重复）→ rejected；评价未过 → rejected（含原因）；
            # 评价通过 → passed
            if task_id is not None:
                from app.services.mining.candidate_store import upsert_candidates
                round_upserts = []
                for r in rejected:
                    round_upserts.append({
                        "name": r.get("name", ""), "expression": r.get("expression", ""),
                        "description": r.get("description", ""),
                        "status": "rejected", "reason": r.get("reason", "")})
                for r in iter_evaluated:
                    round_upserts.append(r)
                for f in round_results:
                    round_upserts.append({
                        "name": f["name"], "expression": f["expression"],
                        "description": f.get("description", ""),
                        "status": "passed", "ic": f.get("ic"), "rank_ic": f.get("rank_ic"),
                        "icir": f.get("icir")})
                await upsert_candidates(task_id, round_upserts, round_no=round_idx + 1)

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
                                  n_candidates: int = None,
                                  universe: str = None) -> dict:
    """迭代挖掘任务包装器：构建默认模板并调用 iterative_mine_factors。"""
    mining_cfg = settings.mining.get("llm", {})
    n_candidates = n_candidates or mining_cfg.get("candidates_per_run", 5)
    template = {
        "prompt": "",
        "llm_prompt": _USER_PROMPT_TEMPLATE,
        "base_features": _AVAILABLE_FIELDS,
        "allowed_ops": mining_cfg.get("allowed_ops", []),
        "ic_threshold": mining_cfg.get("ic_threshold", 0.03),
    }
    return await iterative_mine_factors(
        template, n_rounds=n_rounds, candidates_per_round=n_candidates,
        task_id=task_id, universe=universe,
    )


async def _evaluate_with_validation(expr: str, existing_ic_series: list = None,
                                    baseline_exprs: list = None,
                                    universe: str = None) -> dict:
    """在进程池中运行多维因子验证，带超时保护。

    使用 evaluate_factor_with_validation 替代旧的 evaluate_factor：
    - 样本分割：train/valid/test
    - 滚动 IC + 统计显著性
    - 多样性检测（existing_ic_series）
    - 正交后 IC（baseline_exprs：已有高 IC 因子表达式）
    - 使用 valid_ic 作为主筛选指标

    universe: 标的池（None=config 默认），透传给 evaluate_factor_with_validation。
    """
    from app.services.quant.factor_validator import evaluate_factor_with_validation
    period = settings.quant.get("default_backtest_period", {})
    start = period.get("start", "2020-01-01")
    end = period.get("end", "2024-12-31")
    horizon = settings.mining.get("llm", {}).get("eval_horizon", 5)
    timeout = settings.mining.get("llm", {}).get("eval_timeout_seconds", 120)
    # 滚动窗口重验：默认 60/120 天窗口，验证 IC 稳健性
    roll_windows = settings.mining.get("llm", {}).get("roll_windows", [60, 120])
    # 行业中性化：默认开启（消除行业暴露假 IC）
    ind_neutralize = settings.mining.get("llm", {}).get("industry_neutralize", True)
    # 子样本稳健性：默认开启（时间半区 + 市值分组 IC）
    robustness = settings.mining.get("llm", {}).get("robustness", True)
    # 显著性 / 稳定性阈值透传（config.yaml llm 段可调，与 BH 门分开控制）
    significance_alpha = settings.mining.get("llm", {}).get("significance_alpha", 0.05)
    stability_threshold = settings.mining.get("llm", {}).get("stability_threshold", 0.5)
    from app.core.executor import run_cpu
    return await asyncio.wait_for(
        run_cpu(evaluate_factor_with_validation, expr, start, end,
                horizon=horizon, existing_ic_series=existing_ic_series,
                baseline_exprs=baseline_exprs, roll_windows=roll_windows,
                industry_neutralize_enabled=ind_neutralize,
                robustness_enabled=robustness,
                significance_alpha=significance_alpha,
                stability_threshold=stability_threshold,
                universe=universe),
        timeout=timeout,
    )


def _existing_ic_signature(existing_ic_series: list) -> str:
    """对已有因子 IC 序列集合做轻量签名，纳入缓存 key。

    evaluate_factor_with_validation 的多样性判断依赖 existing_ic_series 的内容，
    因子库变化（新因子入库）后，相同的 expr+universe 会得到不同结果——
    若缓存 key 不含该签名，会命中陈旧的"不重复"判断，放行真实重复的因子。
    """
    if not existing_ic_series:
        return ""
    h = hashlib.md5()
    for s in existing_ic_series:
        h.update(np.asarray(s, dtype=np.float32).tobytes())
    return h.hexdigest()[:16]


def _ic_cache_key(expr: str, diversity: bool = False, universe: str = None,
                  existing_sig: str = "") -> str:
    """生成 IC 缓存 key：表达式 + 评价区间 + horizon + 多样性状态 + 标的池 + 已有因子签名。"""
    period = settings.quant.get("default_backtest_period", {})
    start = period.get("start", "2020-01-01")
    end = period.get("end", "2024-12-31")
    horizon = settings.mining.get("llm", {}).get("eval_horizon", 5)
    raw = f"{expr}|{start}|{end}|{horizon}|{universe or ''}|{'div' if diversity else 'nodiv'}|{existing_sig}"
    return hashlib.md5(raw.encode()).hexdigest()


def _ic_cache_put(key: str, value: dict) -> None:
    """写入 IC 缓存（LRUCache 自动淘汰最久未访问条目）。"""
    _IC_CACHE[key] = value


async def _evaluate_safe_cached(expr: str, existing_ic_series: list = None,
                                universe: str = None) -> dict:
    """带内存缓存的因子评价（使用 evaluate_factor_with_validation）。

    缓存 key 包含多样性状态、标的池与已有因子签名：不同 universe/多样性开关/因子库
    变化的结果分开缓存，避免复用旧缓存导致多样性约束失效或跨池污染。
    注意：缓存是 web 进程侧模块级 LRU，进程池 worker 里的 _IC_CACHE 无法跨调用共享，
    统一走这里才真正生效。
    """
    diversity = bool(existing_ic_series)
    existing_sig = _existing_ic_signature(existing_ic_series)
    key = _ic_cache_key(expr, diversity=diversity, universe=universe, existing_sig=existing_sig)
    if key in _IC_CACHE:
        logger.debug("IC 缓存命中: %s", expr[:40])
        return _IC_CACHE[key]
    result = await _evaluate_with_validation(expr, existing_ic_series=existing_ic_series,
                                             universe=universe)
    _ic_cache_put(key, result)
    return result
