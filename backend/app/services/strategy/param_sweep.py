"""策略参数扫描：自动测试不同 topk/rebalance 组合"""
import json
import asyncio
import logging
from app.core.database import async_session
from app.models.strategy import Strategy

logger = logging.getLogger(__name__)


async def run_param_sweep(
    strategy_id: int,
    topk_list: list[int],
    rebalance_list: list[str],
    start: str,
    end: str,
) -> list[dict]:
    """参数扫描：对策略测试不同 topk 和 rebalance_freq 的组合

    Returns:
        [{"topk": 10, "rebalance": "day", "sharpe": 1.2, "annual_return": 0.15, ...}]
    """
    from app.services.strategy.manager import _load_factor_expressions, _compute_backtest_sync
    from app.services.quant.qlib_init import is_qlib_available

    if not await is_qlib_available():
        return [{"error": "qlib 不可用"}]

    async with async_session() as session:
        s = await session.get(Strategy, strategy_id)
        if s is None:
            return [{"error": "策略不存在"}]

    factor_ids = json.loads(s.factor_ids) if s.factor_ids else []
    factor_meta = await _load_factor_expressions(factor_ids)

    factor_exprs = {}
    weights = {}
    for fid in factor_ids:
        meta = factor_meta.get(fid)
        if not meta:
            continue
        expr_str = meta.get("expression", "")
        if expr_str.startswith("AutoML(") or expr_str.startswith("TextSentiment("):
            continue
        factor_exprs[meta["name"]] = meta["expression"]
        weights[meta["name"]] = meta.get("ic") or 0.0

    if not factor_exprs:
        return [{"error": "无有效因子"}]

    results = []
    total = len(topk_list) * len(rebalance_list)

    for i, topk in enumerate(topk_list):
        for j, rebalance in enumerate(rebalance_list):
            n_drop = min(5, topk // 5)
            try:
                loop = asyncio.get_running_loop()
                computed = await loop.run_in_executor(
                    None, _compute_backtest_sync,
                    factor_exprs, weights, s.combination_method,
                    topk, n_drop, s.benchmark, rebalance, start, end,
                )
                m = computed["metrics"]
                results.append({
                    "topk": topk,
                    "n_drop": n_drop,
                    "rebalance": rebalance,
                    "annual_return": m.get("annual_return"),
                    "annual_volatility": m.get("annual_volatility"),
                    "sharpe": m.get("sharpe"),
                    "max_drawdown": m.get("max_drawdown"),
                    "calmar": m.get("calmar"),
                    "excess_return": m.get("excess_return"),
                    "win_rate": m.get("win_rate"),
                })
                logger.info("参数扫描 %d/%d: topk=%d rebalance=%s sharpe=%.2f",
                            len(results), total, topk, rebalance, m.get("sharpe", 0))
            except Exception as e:
                logger.error("参数扫描失败 topk=%d rebalance=%s: %s", topk, rebalance, e)
                results.append({
                    "topk": topk, "rebalance": rebalance, "error": str(e),
                })

    # 找最优组合
    valid = [r for r in results if "sharpe" in r and r["sharpe"] is not None]
    if valid:
        best = max(valid, key=lambda x: x["sharpe"])
        results.append({"best": best})

    return results
