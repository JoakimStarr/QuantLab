"""策略管理与回测编排：CRUD + 因子组合 + 回测执行 + 结果落库。"""
import json
import logging
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
                          benchmark: str = None, description: str = None,
                          orthogonalize: int = 0, ai_prefs: dict = None) -> dict:
    topk = topk if topk is not None else settings.quant.get("topk", 50)
    n_drop = n_drop if n_drop is not None else settings.quant.get("n_drop", 5)
    benchmark = benchmark or settings.quant.get("benchmark", "SH000300")
    async with async_session() as session:
        s = Strategy(
            name=name, factor_ids=json.dumps(factor_ids),
            combination_method=combination_method, topk=topk, n_drop=n_drop,
            rebalance_freq=rebalance_freq, benchmark=benchmark, description=description,
            orthogonalize=orthogonalize,
            ai_prefs=json.dumps(ai_prefs, ensure_ascii=False) if ai_prefs else None,
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
                           start: str, end: str, orthogonalize: int = 0,
                           backend: str = "qlib", capital: float = None,
                           trade_unit: int = None, deal_price: str = None,
                           slippage_bps: float = None, cost_buy: float = None,
                           cost_sell: float = None, min_cost: float = None,
                           universe: str = None, asset_class: str = "stock") -> dict:
    """同步执行回测计算（在 executor 中调用，不阻塞事件循环）。

    universe: 标的池（None=config 默认），透传给 load_factor_values。
    asset_class: stock/etf，透传给回测后端（ETF 无整手/涨跌停放宽）。
    """
    from app.services.quant.qlib_init import init_qlib
    from app.services.quant.factor_eval import load_factor_values
    from app.services.quant.backtest_engine import combine_factors, run_backtest
    from app.services.quant.portfolio import analyze_portfolio, build_nav_curve

    init_qlib()
    factor_values = {}
    for name, expr in factor_exprs.items():
        factor_values[name] = load_factor_values(expr, start, end, universe=universe)
    score_df = combine_factors(factor_values, weights=weights, method=combination_method,
                               orthogonalize=bool(orthogonalize))
    # 默认过滤北交所股票（防御性，factor_eval 已过滤）
    include_bj = settings.quant.get("include_bj", False)
    if not include_bj and score_df is not None and not score_df.empty:
        inst_codes = score_df.index.get_level_values("instrument")
        bj_mask = inst_codes.str.startswith(("bj", "BJ"))
        if bj_mask.any():
            before = len(inst_codes.unique())
            score_df = score_df[~bj_mask]
            logger.info("回测过滤北交所股票: %d -> %d", before, len(score_df.index.get_level_values("instrument").unique()))
    bt = run_backtest(score_df, start=start, end=end, topk=topk, n_drop=n_drop,
                      benchmark=benchmark, rebalance_freq=rebalance_freq, backend=backend,
                      capital=capital, trade_unit=trade_unit, deal_price=deal_price,
                      slippage_bps=slippage_bps, cost_buy=cost_buy, cost_sell=cost_sell,
                      min_cost=min_cost, asset_class=asset_class)
    returns = bt.get("returns")
    bench = bt.get("benchmark")
    metrics = analyze_portfolio(returns, bench)
    nav_curve = build_nav_curve(returns, bench)
    trades = bt.get("trades") or []

    # 换手率标量化
    turnover_val = None
    bt_turnover = bt.get("turnover")
    try:
        if bt_turnover is not None:
            turnover_val = float(bt_turnover)
    except (TypeError, ValueError):
        turnover_val = None

    return {"metrics": metrics, "nav_curve": nav_curve, "turnover": turnover_val,
            "trades": trades}


async def run_strategy_backtest(strategy_id: int, start: str = None, end: str = None,
                                backend: str = "qlib", capital: float = None,
                                trade_unit: int = None, deal_price: str = None,
                                slippage_bps: float = None, cost_buy: float = None,
                                cost_sell: float = None, min_cost: float = None,
                                universe: str = None, asset_class: str = "stock") -> dict:
    """执行策略回测（CPU 密集计算放入线程池，不阻塞事件循环）。

    流程：加载因子元数据(async) -> 回测计算(executor) -> 落库(async)

    执行/成本参数（用户可选，覆盖 config 默认，随结果落库标注口径）：
        trade_unit / deal_price / slippage_bps / cost_buy / cost_sell / min_cost
    universe: 标的池（None=config 默认）。
    asset_class: stock/etf（ETF 无整手/涨跌停放宽）。
    """
    import asyncio
    strategy = await get_strategy(strategy_id)
    if strategy is None:
        raise ValueError(f"策略 {strategy_id} 不存在")

    period = settings.quant.get("default_backtest_period", {})
    start = start or period.get("start", "2020-01-01")
    end = end or period.get("end", "2024-12-31")
    # 回测区间不超过数据实际范围：前端常传 end=今天，但当日日K 数据 baostock
    # 要到收盘后（~17:30）才入库；且 qlib 回测需要 end 之后一天的数据计算
    # 最后一期收益，end 落在数据末日会越界（index N out of bounds with size N）。
    # 这里用共享函数先做日历收敛（score_df 尚未加载，因子上界由 run_backtest 兜底），
    # 同时让后续因子加载少拉一天的无效数据。
    from app.services.quant.backtest_engine import clamp_backtest_end
    end = clamp_backtest_end(end)
    factor_ids = strategy["factor_ids"] if strategy["factor_ids"] else []
    if not factor_ids:
        raise ValueError("策略未关联任何因子")

    # async: 加载因子元数据
    factor_meta = await _load_factor_expressions(factor_ids)
    factor_exprs = {}
    weights = {}
    skip_reasons = []
    for fid in factor_ids:
        meta = factor_meta.get(fid)
        if not meta:
            continue
        # 非合法 qlib 表达式因子（AutoML/TextSentiment 等占位符）跳过并警告
        expr_str = meta.get("expression", "")
        if expr_str.startswith("AutoML(") or expr_str.startswith("TextSentiment("):
            logger.warning("跳过非 qlib 表达式因子 %s (id=%s, expr=%s...)，不可直接用于 qlib 回测",
                           meta.get("name"), fid, expr_str[:30])
            skip_reasons.append(f"{meta.get('name')}(id={fid},expr={expr_str[:20]})")
            continue
        factor_exprs[meta["name"]] = meta["expression"]
        # ir_weight 用 icir 字段，ic_weight 用 ic 字段
        if strategy["combination_method"] == "ir_weight":
            weights[meta["name"]] = meta.get("icir") or 0.0
        else:
            weights[meta["name"]] = meta.get("ic") or 0.0
    if not factor_exprs:
        raise ValueError(
            f"未找到有效因子表达式。策略包含 {len(factor_ids)} 个因子，"
            f"但全部为 AutoML/TextSentiment 占位符（无法直接用于qlib回测）。"
            f"请在策略中至少添加一个 builtin/llm/symbolic 类型的因子。"
            f"已跳过的因子: {', '.join(skip_reasons)}"
        )

    # sync CPU 密集计算放入线程池
    loop = asyncio.get_running_loop()
    computed = await loop.run_in_executor(
        None, _compute_backtest_sync,
        factor_exprs, weights, strategy["combination_method"],
        strategy["topk"], strategy["n_drop"], strategy["benchmark"],
        strategy["rebalance_freq"], start, end, strategy.get("orthogonalize", 0),
        backend, capital, trade_unit, deal_price, slippage_bps, cost_buy, cost_sell, min_cost,
        universe, asset_class,
    )
    metrics = computed["metrics"]
    nav_curve = computed["nav_curve"]
    trades = computed.get("trades") or []

    # trades 过大时截断落库（保留前 2000 条，足够前端展示与导出），避免 Text 列膨胀
    TRADES_CAP = int(settings.quant.get("trades_cap", 2000))
    if len(trades) > TRADES_CAP:
        logger.info("trades 共 %d 条，落库截断到前 %d 条", len(trades), TRADES_CAP)
        trades = trades[:TRADES_CAP]

    # async: 落库
    async with async_session() as session:
        # 回测口径（执行/成本设置），随结果落库标注，便于对比不同设置
        exec_config = {
            "backend": backend,
            "asset_class": asset_class,
            "trade_unit": trade_unit if trade_unit is not None else "default(100)",
            "deal_price": deal_price or "close",
            "slippage_bps": slippage_bps if slippage_bps is not None else settings.quant.get("slippage_bps", 0),
            "cost_buy": cost_buy if cost_buy is not None else settings.quant.get("cost_buy", 0.0013),
            "cost_sell": cost_sell if cost_sell is not None else settings.quant.get("cost_sell", 0.0023),
            "min_cost": min_cost if min_cost is not None else 5,
        }
        result = BacktestResult(
            strategy_id=strategy_id, start_date=start, end_date=end,
            topk=strategy["topk"], n_drop=strategy["n_drop"],
            rebalance_freq=strategy["rebalance_freq"],
            combination_method=strategy["combination_method"],
            orthogonalize=strategy.get("orthogonalize", 0),
            benchmark=strategy["benchmark"],
            backend=backend,
            initial_capital=capital or settings.quant.get("initial_capital", 100000000),
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
            metrics=json.dumps({**metrics, "topk": strategy["topk"], "n_drop": strategy["n_drop"],
                                "initial_capital": capital or settings.quant.get("initial_capital", 100000000),
                                "exec_config": exec_config}),
            trades=json.dumps(trades, ensure_ascii=False),
        )
        session.add(result)
        await session.commit()
        await session.refresh(result)
        return _result_dict(result)


async def list_backtest_results(strategy_id: int = None, limit: int = 20) -> list[dict]:
    async with async_session() as session:
        q = select(BacktestResult).where(BacktestResult.is_deleted == 0)
        q = q.order_by(BacktestResult.created_at.desc()).limit(limit)
        if strategy_id:
            q = q.where(BacktestResult.strategy_id == strategy_id)
        result = await session.execute(q)
        return [_result_summary(r) for r in result.scalars().all()]


async def get_backtest_result(result_id: int) -> dict:
    async with async_session() as session:
        r = await session.get(BacktestResult, result_id)
        if r is None or r.is_deleted:
            return None
        return _result_dict(r)


async def delete_backtest_result(result_id: int) -> bool:
    """软删除回测结果（is_deleted=1），前端可手动清理重复/过期记录。"""
    async with async_session() as session:
        r = await session.get(BacktestResult, result_id)
        if r is None:
            return False
        r.is_deleted = 1
        await session.commit()
        return True


def _strategy_dict(r: Strategy) -> dict:
    ai_prefs = json.loads(r.ai_prefs) if r.ai_prefs else None
    return {
        "id": r.id, "name": r.name, "description": r.description,
        "factor_ids": json.loads(r.factor_ids) if r.factor_ids else [],
        "combination_method": r.combination_method,
        "topk": r.topk, "n_drop": r.n_drop, "rebalance_freq": r.rebalance_freq,
        "benchmark": r.benchmark, "status": r.status,
        "orthogonalize": r.orthogonalize,
        "ai_prefs": ai_prefs,
        # 兼容便捷字段：capital 从 ai_prefs 派生（前端回测预填用）
        "capital": (ai_prefs or {}).get("capital"),
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _result_dict(r: BacktestResult) -> dict:
    return {
        "id": r.id, "strategy_id": r.strategy_id,
        "start_date": r.start_date, "end_date": r.end_date,
        "topk": r.topk, "n_drop": r.n_drop, "rebalance_freq": r.rebalance_freq,
        "combination_method": r.combination_method,
        "orthogonalize": r.orthogonalize,
        "benchmark": r.benchmark,
        "backend": r.backend,
        "initial_capital": r.initial_capital,
        "annual_return": r.annual_return, "annual_volatility": r.annual_volatility,
        "sharpe": r.sharpe, "sortino": r.sortino,
        "max_drawdown": r.max_drawdown, "calmar": r.calmar,
        "turnover": r.turnover,
        "win_rate": r.win_rate,
        "benchmark_return": r.benchmark_return, "excess_return": r.excess_return,
        "nav_curve": json.loads(r.nav_curve) if r.nav_curve else None,
        "metrics": json.loads(r.metrics) if r.metrics else None,
        "trades": json.loads(r.trades) if r.trades else [],
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _result_summary(r: BacktestResult) -> dict:
    """列表接口的轻量摘要：不含 trades/nav_curve/metrics 大字段。

    逐笔成交明细可达 2000 条、净值曲线可达数千点，单条 JSON 300KB+；
    列表场景（策略回测页/回测对比选择器/首页最近回测）只用到标量指标，
    省略大字段可把列表载荷从 344KB/条 降到 <1KB/条，点击"结果"不再二次下载全量。
    """
    return {
        "id": r.id, "strategy_id": r.strategy_id,
        "start_date": r.start_date, "end_date": r.end_date,
        "topk": r.topk, "n_drop": r.n_drop, "rebalance_freq": r.rebalance_freq,
        "combination_method": r.combination_method,
        "orthogonalize": r.orthogonalize,
        "benchmark": r.benchmark,
        "backend": r.backend,
        "initial_capital": r.initial_capital,
        "annual_return": r.annual_return, "annual_volatility": r.annual_volatility,
        "sharpe": r.sharpe, "sortino": r.sortino,
        "max_drawdown": r.max_drawdown, "calmar": r.calmar,
        "turnover": r.turnover,
        "win_rate": r.win_rate,
        "benchmark_return": r.benchmark_return, "excess_return": r.excess_return,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
