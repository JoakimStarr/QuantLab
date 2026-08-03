"""AI 策略服务：AI 生成策略 / AI 参数建议 / AI 策略复盘。

复用现有基础设施：
- app.services.ai.llm_json.call_llm_json()：LLM 调用（failover + JSON 容错解析）
- factor library / strategy manager：查询因子与创建策略
"""
import json
import logging

from app.services.ai.llm_json import call_llm_json

logger = logging.getLogger(__name__)

_MAX_STRATEGY_FACTORS = 5
_MIN_STRATEGY_FACTORS = 2


async def _load_qualified_factors(limit: int = 30) -> list[dict]:
    """加载因子库中已达标（|IC|>=阈值 且 icir 不为空）的因子，按 ICIR 降序。"""
    from sqlalchemy import select, func
    from app.core.config import settings
    from app.core.database import async_session
    from app.models.factor import Factor
    from app.services.factor.library import _to_dict

    threshold = settings.mining.get("llm", {}).get("ic_threshold", 0.03)
    async with async_session() as session:
        result = await session.execute(
            select(Factor)
            .where(Factor.status == "active")
            .where(Factor.ic.isnot(None))
            .where(func.abs(Factor.ic) >= threshold)
            .order_by(Factor.icir.desc().nullslast(), Factor.ic.desc())
            .limit(limit)
        )
        factors = [_to_dict(r) for r in result.scalars().all()]
    return factors


async def _load_correlation_hint(factor_ids: list[int]) -> str:
    """尝试生成因子间相关性提示（尽力而为，失败返回 None 提示）。

    基于 compare_factors 的 ic_timeseries（每日 IC 序列）计算因子间 IC 相关性。
    """
    try:
        from app.services.factor.factor_compare import compare_factors
        # 依赖真实数据，失败时静默降级
        result = await compare_factors(factor_ids)
        ts = result.get("ic_timeseries") or []
        if not ts:
            return None
        # 构建 date -> {factor_id: ic} 面板
        panel = {}
        for row in ts:
            panel.setdefault(row["date"], {})[row["factor_id"]] = row["ic"]
        # 提取因子两两相关
        pairs = []
        import numpy as np
        dates = sorted(panel.keys())
        for i in range(len(factor_ids)):
            for j in range(i + 1, len(factor_ids)):
                a, b = factor_ids[i], factor_ids[j]
                ica = [panel[d].get(a) for d in dates]
                icb = [panel[d].get(b) for d in dates]
                valid = [(x, y) for x, y in zip(ica, icb) if x is not None and y is not None]
                if len(valid) >= 10:
                    xs = [x for x, _ in valid]
                    ys = [y for _, y in valid]
                    corr = float(np.corrcoef(xs, ys)[0, 1])
                    pairs.append(f"因子{a}与因子{b} IC相关性 {corr:.2f}")
        return "；".join(pairs) if pairs else None
    except Exception as e:
        logger.debug("相关性计算失败，降级: %s", e)
        return None


async def generate_strategy_with_ai(
    universe: str = None,
    start: str = None,
    end: str = None,
    prefer_factor_ids: list[int] = None,
) -> dict:
    """AI 生成策略：参考因子评价自动推荐因子组合与参数。

    Returns:
        {"strategy": {...}, "rationale": "...", "raw": {...}}
    """
    from app.services.strategy.ai_prompts import build_strategy_gen_prompt
    from app.services.strategy.manager import create_strategy

    factors = await _load_qualified_factors()
    if not factors:
        raise ValueError("因子库中没有已达标（IC 显著）的因子，请先导入 Alpha158 或补算指标")

    # 可选：用户指定偏好的因子子集
    if prefer_factor_ids:
        factors = [f for f in factors if f["id"] in prefer_factor_ids] or factors

    corr_hint = await _load_correlation_hint([f["id"] for f in factors[:10]])
    constraints = []
    if universe:
        constraints.append(f"股票池: {universe}")
    if start and end:
        constraints.append(f"回测区间: {start} ~ {end}")
    constraint_str = "；".join(constraints) or None

    messages = build_strategy_gen_prompt(
        factors=factors[:15],
        correlation_hint=corr_hint,
        constraints=constraint_str,
    )
    raw = await call_llm_json(messages)

    factor_ids = raw.get("factor_ids") or []
    factor_ids = [int(x) for x in factor_ids if str(x).isdigit()]
    factor_ids = [x for x in factor_ids if any(f["id"] == x for f in factors)]
    if len(factor_ids) < _MIN_STRATEGY_FACTORS:
        raise ValueError("AI 建议的因子不足，请重试或补充已达标因子")

    factor_ids = factor_ids[:_MAX_STRATEGY_FACTORS]
    topk = int(raw.get("topk") or 50)
    n_drop = int(raw.get("n_drop") or 5)
    rebalance = raw.get("rebalance_freq") or "day"
    if rebalance not in ("day", "week", "month"):
        rebalance = "day"
    method = raw.get("combination_method") or "equal_weight"

    selected = [f for f in factors if f["id"] in factor_ids]
    name = "AI策略-" + "+".join(f["name"][:6] for f in selected)[:30]
    rationale = raw.get("rationale") or "AI 根据因子 IC/ICIR 与相关性自动推荐"

    strategy = await create_strategy(
        name=name,
        factor_ids=factor_ids,
        combination_method=method,
        topk=topk,
        n_drop=n_drop,
        rebalance_freq=rebalance,
        description=f"[AI生成] {rationale}",
    )
    return {
        "strategy": strategy,
        "rationale": rationale,
        "raw": raw,
        "factors": [{"id": f["id"], "name": f["name"]} for f in selected],
    }


async def suggest_params_with_ai(strategy_id: int) -> dict:
    """AI 参数建议：基于因子组合（+可选历史回测）推荐参数范围。"""
    from app.services.strategy.ai_prompts import build_param_suggestion_prompt
    from app.services.strategy.manager import get_strategy, list_backtest_results

    strategy = await get_strategy(strategy_id)
    if not strategy:
        raise ValueError(f"策略不存在: {strategy_id}")

    factor_ids = strategy.get("factor_ids") or []
    if not isinstance(factor_ids, list):
        try:
            factor_ids = json.loads(factor_ids)
        except Exception:
            factor_ids = []

    factors = await _load_qualified_factors()
    selected = [f for f in factors if f["id"] in factor_ids]
    summary = "\n".join(
        f"- {f['name']} | IC={f.get('ic'):.4f} | ICIR={f.get('icir') or '--'}"
        for f in selected
    ) or f"(策略 {strategy_id} 的因子未在达标列表)"

    backtest_summary = None
    try:
        results = await list_backtest_results(strategy_id, limit=5)
        if results:
            r = results[0]
            backtest_summary = (
                f"最新回测: 收益={r.get('total_return')}, "
                f"夏普={r.get('sharpe')}, 最大回撤={r.get('max_drawdown')}, "
                f"topk={r.get('topk')}, 调仓={r.get('rebalance_freq')}"
            )
    except Exception as e:
        logger.debug("历史回测加载失败: %s", e)

    messages = build_param_suggestion_prompt(summary, backtest_summary)
    raw = await call_llm_json(messages)
    return {"suggestions": raw, "strategy_id": strategy_id}


async def review_backtest_with_ai(strategy_id: int, result_id: int = None) -> dict:
    """AI 策略复盘：解读回测结果，生成结构化文字报告。

    Returns:
        {"review": {...}, "metrics": {...}, "key_events": [...]}
    """
    from app.services.strategy.ai_prompts import build_review_prompt
    from app.services.strategy.manager import get_backtest_result, get_strategy

    strategy = await get_strategy(strategy_id)
    if not strategy:
        raise ValueError(f"策略不存在: {strategy_id}")

    result = await get_backtest_result(result_id) if result_id else None
    if result is None:
        from app.services.strategy.manager import list_backtest_results
        results = await list_backtest_results(strategy_id, limit=1)
        if not results:
            raise ValueError(f"策略 {strategy_id} 没有回测结果可复盘")
        result = results[0]

    metrics = _extract_metrics(result)
    key_events = _extract_key_events(result)

    strategy_summary = {
        "name": strategy.get("name"),
        "factor_ids": strategy.get("factor_ids"),
        "topk": strategy.get("topk"),
        "n_drop": strategy.get("n_drop"),
        "rebalance_freq": strategy.get("rebalance_freq"),
        "benchmark": strategy.get("benchmark"),
    }

    messages = build_review_prompt(strategy_summary, metrics, key_events)
    raw = await call_llm_json(messages)
    return {"review": raw, "metrics": metrics, "key_events": key_events}


def _extract_metrics(result: dict) -> dict:
    """从回测结果提取绩效指标（尽力而为）。"""
    keys = [
        "total_return", "annual_return", "sharpe", "sortino",
        "max_drawdown", "calmar", "volatility", "win_rate",
        "turnover", "benchmark_return", "excess_return", "n_trades",
    ]
    metrics = {}
    for k in keys:
        if k in result and result[k] is not None:
            v = result[k]
            metrics[k] = round(v, 4) if isinstance(v, float) else v
    return metrics


def _extract_key_events(result: dict) -> list:
    """从回测结果提取关键事件（回撤/收益转折点等，尽力而为）。"""
    events = []
    drawdowns = result.get("drawdowns") or result.get("max_drawdown_details")
    if isinstance(drawdowns, list):
        for d in drawdowns[:3]:
            if isinstance(d, dict):
                events.append({
                    "date": d.get("date") or d.get("start"),
                    "type": "回撤",
                    "description": f"回撤 {d.get('value')}",
                    "impact": "对净值的负面影响",
                })
    trades = result.get("trades")
    if isinstance(trades, list) and trades:
        events.append({
            "date": None, "type": "交易", "description": f"共 {len(trades)} 笔交易",
            "impact": "交易成本与换手影响",
        })
    return events
