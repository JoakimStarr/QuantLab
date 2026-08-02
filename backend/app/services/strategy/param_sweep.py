"""策略参数扫描：自动测试不同 topk/rebalance 组合。

优化（批次4-回测）：
1. 数据预加载：扫描前一次性加载所有因子值并组合成 score_df，
   避免每个参数组合重复加载因子（N 个组合从 N 次 IO 降为 1 次）
2. 参数扫描并行：用 ThreadPoolExecutor 并行运行不同参数组合的 run_backtest
   （qlib C 扩展释放 GIL，多线程可真并行价格回测计算）
3. 回测结果三级缓存：
   a. 内存 LRU（128 条）：进程内秒级命中
   b. DB 持久化（backtest_result 表）：进程重启后仍可命中，避免重复回测
   c. 实时计算：缓存全 miss 时才真正跑回测
   查询顺序：内存 LRU → DB → 计算；计算成功后同时写内存 + DB
"""
import asyncio
import hashlib
import json
import logging
from collections import OrderedDict

from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session
from app.models.backtest_result import BacktestResult
from app.models.strategy import Strategy

logger = logging.getLogger(__name__)

# 回测结果内存缓存：LRU，最多 128 条
# key = md5(strategy_id|topk|n_drop|rebalance|start|end|backend)
# value = metrics dict（含 sharpe/annual_return/max_drawdown 等）
_BT_CACHE: OrderedDict[str, dict] = OrderedDict()
_BT_CACHE_MAX = 128


def _bt_cache_key(strategy_id: int, topk: int, n_drop: int,
                  rebalance: str, start: str, end: str, backend: str) -> str:
    """生成回测结果缓存 key。"""
    raw = f"{strategy_id}|{topk}|{n_drop}|{rebalance}|{start}|{end}|{backend}"
    return hashlib.md5(raw.encode()).hexdigest()


def _bt_cache_get(key: str) -> dict | None:
    """从缓存取回测结果；命中则移到末尾（LRU）。"""
    if key in _BT_CACHE:
        _BT_CACHE.move_to_end(key)
        return _BT_CACHE[key]
    return None


def _bt_cache_put(key: str, value: dict) -> None:
    """写入缓存；超过容量淘汰最旧。"""
    _BT_CACHE[key] = value
    _BT_CACHE.move_to_end(key)
    if len(_BT_CACHE) > _BT_CACHE_MAX:
        _BT_CACHE.popitem(last=False)


def clear_bt_cache() -> int:
    """清空回测结果内存缓存，返回清空条数（供调试/手动刷新用）。

    注意：仅清内存 LRU，DB 持久化缓存保留（如需清 DB 请用后台管理接口）。
    """
    n = len(_BT_CACHE)
    _BT_CACHE.clear()
    return n


# ---------------- DB 持久化缓存 ----------------

async def _db_cache_batch_get(
    strategy_id: int, start: str, end: str,
    params: list[tuple[int, int, str]],
) -> dict[tuple[int, int, str], dict]:
    """批量从 backtest_result 表查参数扫描结果。

    Args:
        params: [(topk, n_drop, rebalance), ...] 待查参数组合
    Returns:
        {(topk, n_drop, rebalance): result_dict, ...} 命中的记录
    """
    if not params:
        return {}
    hits = {}
    try:
        async with async_session() as session:
            # 一次查出该策略+区间内所有候选参数的结果，本地按 (topk,n_drop,rebalance) 匹配
            q = select(BacktestResult).where(
                BacktestResult.strategy_id == strategy_id,
                BacktestResult.start_date == start,
                BacktestResult.end_date == end,
                BacktestResult.topk.isnot(None),
                BacktestResult.rebalance_freq.isnot(None),
            )
            rows = (await session.execute(q)).scalars().all()
            # 建索引：{(topk, n_drop, rebalance): row}
            row_map = {}
            for r in rows:
                key = (r.topk, r.n_drop, r.rebalance_freq)
                # 同参数可能有多条（历史），取最新一条（id 最大）
                if key not in row_map or r.id > row_map[key].id:
                    row_map[key] = r
            for (topk, n_drop, rebalance) in params:
                r = row_map.get((topk, n_drop, rebalance))
                if r is None:
                    continue
                hits[(topk, n_drop, rebalance)] = _db_row_to_result(r)
    except Exception as e:
        logger.warning("DB 缓存查询失败（降级为实时计算）: %s", e)
    return hits


def _db_row_to_result(r: BacktestResult) -> dict:
    """把 backtest_result 行转成 _run_single_backtest 同结构的 result dict。"""
    return {
        "metrics": json.loads(r.metrics) if r.metrics else {},
        "nav_curve": json.loads(r.nav_curve) if r.nav_curve else None,
        "turnover": r.turnover,
    }


async def _db_cache_put(strategy_id: int, start: str, end: str,
                        topk: int, n_drop: int, rebalance: str,
                        result: dict) -> None:
    """把单个参数扫描结果写入 backtest_result 表。

    失败不阻塞扫描流程（缓存写入失败仅影响下次命中率）。
    """
    if "error" in result:
        return
    metrics = result.get("metrics", {})
    nav_curve = result.get("nav_curve")
    try:
        async with async_session() as session:
            rec = BacktestResult(
                strategy_id=strategy_id, start_date=start, end_date=end,
                topk=topk, n_drop=n_drop, rebalance_freq=rebalance,
                annual_return=metrics.get("annual_return"),
                annual_volatility=metrics.get("annual_volatility"),
                sharpe=metrics.get("sharpe"),
                sortino=metrics.get("sortino"),
                max_drawdown=metrics.get("max_drawdown"),
                calmar=metrics.get("calmar"),
                turnover=result.get("turnover"),
                win_rate=metrics.get("win_rate"),
                benchmark_return=metrics.get("benchmark_return"),
                excess_return=metrics.get("excess_return"),
                nav_curve=json.dumps(nav_curve) if nav_curve else None,
                metrics=json.dumps(metrics),
            )
            session.add(rec)
            await session.commit()
    except Exception as e:
        logger.warning("DB 缓存写入失败 topk=%s rebalance=%s: %s", topk, rebalance, e)


def _preload_score_df(factor_exprs: dict, weights: dict,
                      combination_method: str, start: str, end: str,
                      orthogonalize: bool = False):
    """预加载所有因子值并组合成 score_df（同步，在 executor 中调用）。

    替代 _compute_backtest_sync 内部每个组合重复加载因子的逻辑：
    扫描 N 个参数组合只加载 1 次因子数据。
    """
    from app.services.quant.qlib_init import init_qlib
    from app.services.quant.factor_eval import load_factor_values
    from app.services.quant.backtest_engine import combine_factors

    init_qlib()
    factor_values = {}
    for name, expr in factor_exprs.items():
        try:
            factor_values[name] = load_factor_values(expr, start, end)
        except Exception as e:
            logger.warning("预加载因子 %s 失败: %s", name, e)

    if not factor_values:
        raise ValueError("所有因子预加载失败")

    score_df = combine_factors(
        factor_values, weights=weights, method=combination_method,
        orthogonalize=orthogonalize,
    )

    # 防御性过滤北交所
    include_bj = settings.quant.get("include_bj", False)
    if not include_bj and score_df is not None and not score_df.empty:
        inst_codes = score_df.index.get_level_values("instrument")
        bj_mask = inst_codes.str.startswith(("bj", "BJ"))
        if bj_mask.any():
            score_df = score_df[~bj_mask]

    return score_df


def _run_single_backtest(score_df, topk: int, n_drop: int, benchmark: str,
                         rebalance: str, start: str, end: str,
                         backend: str = "qlib") -> dict:
    """单次回测（模块级函数，可在线程池并行调用）。

    接收预加载的 score_df，避免重复加载因子数据。
    """
    from app.services.quant.backtest_engine import run_backtest
    from app.services.quant.portfolio import analyze_portfolio, build_nav_curve

    bt = run_backtest(
        score_df, start=start, end=end, topk=topk, n_drop=n_drop,
        benchmark=benchmark, rebalance_freq=rebalance, backend=backend,
    )
    returns = bt.get("returns")
    bench = bt.get("benchmark")
    metrics = analyze_portfolio(returns, bench)
    nav_curve = build_nav_curve(returns, bench)

    turnover_val = None
    bt_turnover = bt.get("turnover")
    try:
        if bt_turnover is not None:
            turnover_val = float(bt_turnover)
    except (TypeError, ValueError):
        turnover_val = None

    return {"metrics": metrics, "nav_curve": nav_curve, "turnover": turnover_val}


async def run_param_sweep(
    strategy_id: int,
    topk_list: list[int],
    rebalance_list: list[str],
    start: str,
    end: str,
    backend: str = "qlib",
) -> list[dict]:
    """参数扫描：对策略测试不同 topk 和 rebalance_freq 的组合。

    优化：
    - 数据预加载：一次加载所有因子值并组合 score_df
    - 并行扫描：ThreadPoolExecutor 并行运行各参数组合（qlib C 扩展释放 GIL）
    - 结果缓存：相同参数组合秒级返回

    Args:
        strategy_id: 策略 id
        topk_list: 候选 topk 值
        rebalance_list: 候选调仓频率
        start/end: 回测区间
        backend: 回测后端 qlib/self

    Returns:
        [{"topk":10,"rebalance":"day","sharpe":...}, ..., {"best": {...}}]
    """
    from app.services.strategy.manager import _load_factor_expressions
    from app.services.quant.qlib_init import is_qlib_available
    from app.core.executor import get_io_executor

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

    # ===== Phase 1: 数据预加载（一次加载因子 + 组合 score_df）=====
    loop = asyncio.get_running_loop()
    orthogonalize = int(s.orthogonalize) if hasattr(s, "orthogonalize") else 0
    try:
        score_df = await loop.run_in_executor(
            get_io_executor(),
            _preload_score_df,
            factor_exprs, weights, s.combination_method, start, end,
            bool(orthogonalize),
        )
    except Exception as e:
        logger.error("参数扫描预加载因子失败: %s", e)
        return [{"error": f"因子预加载失败: {e}"}]

    # ===== Phase 2: 三级缓存查询 + 并行扫描 =====
    # 2a. 内存 LRU 缓存
    pending = []  # [(topk, n_drop, rebalance, cache_key), ...]
    cached_results = {}  # cache_key -> result

    for topk in topk_list:
        for rebalance in rebalance_list:
            n_drop = min(5, topk // 5)
            cache_key = _bt_cache_key(
                strategy_id, topk, n_drop, rebalance, start, end, backend,
            )
            cached = _bt_cache_get(cache_key)
            if cached is not None:
                logger.info("参数扫描内存缓存命中: topk=%d rebalance=%s", topk, rebalance)
                cached_results[cache_key] = cached
            else:
                pending.append((topk, n_drop, rebalance, cache_key))

    # 2b. DB 持久化缓存（对内存未命中的批量查）
    # 进程重启后内存清空，DB 缓存仍可命中，避免重复回测
    if pending:
        db_params = [(t, nd, r) for t, nd, r, _ in pending]
        db_hits = await _db_cache_batch_get(strategy_id, start, end, db_params)
        still_pending = []
        for topk, n_drop, rebalance, cache_key in pending:
            hit = db_hits.get((topk, n_drop, rebalance))
            if hit is not None:
                logger.info("参数扫描 DB 缓存命中: topk=%d rebalance=%s", topk, rebalance)
                # 回填内存 LRU，下次进程内直接命中
                _bt_cache_put(cache_key, hit)
                cached_results[cache_key] = hit
            else:
                still_pending.append((topk, n_drop, rebalance, cache_key))
        pending = still_pending

    # 2c. 实时并行计算（缓存全 miss 的组合）
    # 用 ThreadPoolExecutor：qlib 的 C 扩展（计算价格回测）会释放 GIL，多线程可并行
    # 比 ProcessPoolExecutor 快（避免子进程 qlib 重新初始化 + score_df 序列化开销）
    new_results = {}
    if pending:
        executor = get_io_executor()

        async def _run_one(topk, n_drop, rebalance, cache_key):
            try:
                computed = await loop.run_in_executor(
                    executor, _run_single_backtest,
                    score_df, topk, n_drop, s.benchmark, rebalance, start, end, backend,
                )
                # 双写：内存 LRU + DB 持久化（DB 写失败不阻塞）
                _bt_cache_put(cache_key, computed)
                await _db_cache_put(strategy_id, start, end,
                                    topk, n_drop, rebalance, computed)
                return cache_key, computed
            except Exception as e:
                logger.error("参数扫描失败 topk=%d rebalance=%s: %s", topk, rebalance, e)
                return cache_key, {"error": str(e), "metrics": {}, "turnover": None}

        tasks = [_run_one(t, nd, r, k) for t, nd, r, k in pending]
        done = await asyncio.gather(*tasks, return_exceptions=True)
        for item in done:
            if isinstance(item, Exception):
                logger.error("参数扫描任务异常: %s", item)
                continue
            ck, res = item
            new_results[ck] = res

    # ===== Phase 3: 汇总结果（保持参数组合顺序）=====
    results = []
    for topk in topk_list:
        for rebalance in rebalance_list:
            n_drop = min(5, topk // 5)
            cache_key = _bt_cache_key(
                strategy_id, topk, n_drop, rebalance, start, end, backend,
            )
            computed = cached_results.get(cache_key) or new_results.get(cache_key)
            if computed is None or "error" in computed:
                results.append({
                    "topk": topk, "n_drop": n_drop, "rebalance": rebalance,
                    "error": computed.get("error") if computed else "无结果",
                })
                continue
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
                "turnover": computed.get("turnover"),
                "cached": cache_key in cached_results,
            })
            logger.info("参数扫描 topk=%d rebalance=%s sharpe=%.2f cached=%s",
                        topk, rebalance, m.get("sharpe", 0) or 0,
                        cache_key in cached_results)

    # 找最优组合
    valid = [r for r in results if "sharpe" in r and r["sharpe"] is not None]
    if valid:
        best = max(valid, key=lambda x: x["sharpe"])
        results.append({"best": best})

    return results
