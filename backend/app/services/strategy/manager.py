"""策略管理与回测编排：CRUD + 因子组合 + 回测执行 + 结果落库。"""
import json
import logging
from datetime import datetime
from sqlalchemy import select, func
from app.core.database import async_session
from app.core.config import settings
from app.models.strategy import Strategy
from app.models.backtest_result import BacktestResult
from app.models.factor import Factor

logger = logging.getLogger(__name__)


async def list_strategies(status: str = "active") -> tuple[list[dict], int]:
    async with async_session() as session:
        count_result = await session.execute(
            select(func.count()).select_from(Strategy).where(Strategy.status == status)
        )
        total = count_result.scalar() or 0
        result = await session.execute(
            select(Strategy).where(Strategy.status == status).order_by(Strategy.created_at.desc())
        )
        return [_strategy_dict(r) for r in result.scalars().all()], total


async def get_strategy(strategy_id: int) -> dict:
    async with async_session() as session:
        r = await session.get(Strategy, strategy_id)
        return _strategy_dict(r) if r else None


async def create_strategy(name: str, factor_ids: list[int], combination_method: str = "equal_weight",
                          topk: int = None, n_drop: int = None, rebalance_freq: str = "day",
                          benchmark: str = None, description: str = None) -> dict:
    topk = topk or settings.quant.get("topk", 50)
    n_drop = n_drop or settings.quant.get("n_drop", 5)
    benchmark = benchmark or settings.quant.get("benchmark", "SH000300")
    async with async_session() as session:
        s = Strategy(
            name=name, factor_ids=json.dumps(factor_ids),
            combination_method=combination_method, topk=topk, n_drop=n_drop,
            rebalance_freq=rebalance_freq, benchmark=benchmark, description=description,
        )
        session.add(s)
        await session.commit()
        await session.refresh(s)
        return _strategy_dict(s)


async def archive_strategy(strategy_id: int) -> bool:
    async with async_session() as session:
        r = await session.get(Strategy, strategy_id)
        if r is None:
            return False
        r.status = "archived"
        await session.commit()
        return True


async def _load_factor_expressions(factor_ids: list[int]) -> dict:
    """{factor_id: {name, expression, ic}}"""
    async with async_session() as session:
        result = await session.execute(select(Factor).where(Factor.id.in_(factor_ids)))
        factors = {f.id: {"name": f.name, "expression": f.expression, "ic": f.ic} for f in result.scalars().all()}
    return factors


def _compute_backtest_sync(factor_exprs: dict, weights: dict, combination_method: str,
                           topk: int, n_drop: int, benchmark: str, rebalance_freq: str,
                           start: str, end: str) -> dict:
    """同步执行回测计算（在 executor 中调用，不阻塞事件循环）。"""
    from app.services.quant.qlib_init import init_qlib
    from app.services.quant.factor_eval import load_factor_values
    from app.services.quant.backtest_engine import combine_factors, run_backtest
    from app.services.quant.portfolio import analyze_portfolio, build_nav_curve

    init_qlib()
    factor_values = {}
    for name, expr in factor_exprs.items():
        factor_values[name] = load_factor_values(expr, start, end)
    score_df = combine_factors(factor_values, weights=weights, method=combination_method)
    bt = run_backtest(score_df, start=start, end=end, topk=topk, n_drop=n_drop,
                      benchmark=benchmark, rebalance_freq=rebalance_freq)
    returns = bt.get("returns")
    bench = bt.get("benchmark")
    metrics = analyze_portfolio(returns, bench)
    nav_curve = build_nav_curve(returns, bench)

    # 换手率标量化
    turnover_val = None
    bt_turnover = bt.get("turnover")
    try:
        if bt_turnover is not None:
            turnover_val = float(bt_turnover)
    except (TypeError, ValueError):
        turnover_val = None

    return {"metrics": metrics, "nav_curve": nav_curve, "turnover": turnover_val}


async def run_strategy_backtest(strategy_id: int, start: str = None, end: str = None) -> dict:
    """执行策略回测（CPU 密集计算放入线程池，不阻塞事件循环）。

    流程：加载因子元数据(async) -> 回测计算(executor) -> 落库(async)
    """
    import asyncio
    strategy = await get_strategy(strategy_id)
    if strategy is None:
        raise ValueError(f"策略 {strategy_id} 不存在")

    period = settings.quant.get("default_backtest_period", {})
    start = start or period.get("start", "2020-01-01")
    end = end or period.get("end", "2024-12-31")
    factor_ids = strategy["factor_ids"] if strategy["factor_ids"] else []
    if not factor_ids:
        raise ValueError("策略未关联任何因子")

    # async: 加载因子元数据
    factor_meta = await _load_factor_expressions(factor_ids)
    factor_exprs = {}
    weights = {}
    for fid in factor_ids:
        meta = factor_meta.get(fid)
        if not meta:
            continue
        # 非合法 qlib 表达式因子（AutoML/TextSentiment 等占位符）跳过并警告
        expr_str = meta.get("expression", "")
        if expr_str.startswith("AutoML(") or expr_str.startswith("TextSentiment("):
            logger.warning("跳过非 qlib 表达式因子 %s (id=%s, expr=%s...)，不可直接用于 qlib 回测",
                           meta.get("name"), fid, expr_str[:30])
            continue
        factor_exprs[meta["name"]] = meta["expression"]
        weights[meta["name"]] = meta.get("ic") or 0.0
    if not factor_exprs:
        raise ValueError("未找到有效因子表达式")

    # sync CPU 密集计算放入线程池
    loop = asyncio.get_running_loop()
    computed = await loop.run_in_executor(
        None, _compute_backtest_sync,
        factor_exprs, weights, strategy["combination_method"],
        strategy["topk"], strategy["n_drop"], strategy["benchmark"],
        strategy["rebalance_freq"], start, end,
    )
    metrics = computed["metrics"]
    nav_curve = computed["nav_curve"]

    # async: 落库
    async with async_session() as session:
        result = BacktestResult(
            strategy_id=strategy_id, start_date=start, end_date=end,
            annual_return=metrics.get("annual_return"),
            annual_volatility=metrics.get("annual_volatility"),
            sharpe=metrics.get("sharpe"),
            sortino=metrics.get("sortino"),
            max_drawdown=metrics.get("max_drawdown"),
            calmar=metrics.get("calmar"),
            turnover=computed["turnover"],
            win_rate=metrics.get("win_rate"),
            benchmark_return=metrics.get("benchmark_return"),
            excess_return=metrics.get("excess_return"),
            nav_curve=json.dumps(nav_curve),
            metrics=json.dumps({**metrics, "topk": strategy["topk"], "n_drop": strategy["n_drop"]}),
        )
        session.add(result)
        await session.commit()
        await session.refresh(result)
        return _result_dict(result)


async def list_backtest_results(strategy_id: int = None, limit: int = 20) -> list[dict]:
    async with async_session() as session:
        q = select(BacktestResult).order_by(BacktestResult.created_at.desc()).limit(limit)
        if strategy_id:
            q = q.where(BacktestResult.strategy_id == strategy_id)
        result = await session.execute(q)
        return [_result_dict(r) for r in result.scalars().all()]


async def get_backtest_result(result_id: int) -> dict:
    async with async_session() as session:
        r = await session.get(BacktestResult, result_id)
        return _result_dict(r) if r else None


def _strategy_dict(r: Strategy) -> dict:
    return {
        "id": r.id, "name": r.name, "description": r.description,
        "factor_ids": json.loads(r.factor_ids) if r.factor_ids else [],
        "combination_method": r.combination_method,
        "topk": r.topk, "n_drop": r.n_drop, "rebalance_freq": r.rebalance_freq,
        "benchmark": r.benchmark, "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _result_dict(r: BacktestResult) -> dict:
    return {
        "id": r.id, "strategy_id": r.strategy_id,
        "start_date": r.start_date, "end_date": r.end_date,
        "annual_return": r.annual_return, "annual_volatility": r.annual_volatility,
        "sharpe": r.sharpe, "sortino": r.sortino,
        "max_drawdown": r.max_drawdown, "calmar": r.calmar,
        "turnover": r.turnover,
        "win_rate": r.win_rate,
        "benchmark_return": r.benchmark_return, "excess_return": r.excess_return,
        "nav_curve": json.loads(r.nav_curve) if r.nav_curve else None,
        "metrics": json.loads(r.metrics) if r.metrics else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
