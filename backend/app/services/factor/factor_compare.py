"""因子对比与衰减分析服务"""
import json
import asyncio
import logging
import time
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from sqlalchemy import select
from app.core.database import async_session
from app.models.factor import Factor
from app.services.quant.factor_eval import (
    load_factor_values, load_label, compute_ic, compute_decay, compute_daily_ic_series
)

logger = logging.getLogger(__name__)

# 跨请求 TTL 缓存：对比结果是 CPU/IO 重活，同一组因子短时间内重复对比直接复用
_compare_cache: dict = {}
_COMPARE_CACHE_TTL = 300  # 秒


def _cache_get(key):
    item = _compare_cache.get(key)
    if item and time.time() - item["ts"] < _COMPARE_CACHE_TTL:
        return item["data"]
    return None


def _cache_set(key, data):
    _compare_cache[key] = {"ts": time.time(), "data": data}
    if len(_compare_cache) > 64:
        # 简单淘汰最旧的 32 条，避免缓存无限膨胀
        for k in list(_compare_cache)[:32]:
            _compare_cache.pop(k, None)


async def compare_factors(factor_ids: list[int], start: str, end: str) -> dict:
    """对比多个因子的 IC 指标和衰减曲线

    Returns:
        {
            "factors": [{id, name, expression, ic, rank_ic, icir, turnover, decay}],
            "ic_comparison": [{date, factor_id, ic}],  # IC 时序对比
            "decay_comparison": [{lag, factor_id, ic}],  # 衰减对比
        }
    """
    # 加载因子元数据
    async with async_session() as session:
        result = await session.execute(select(Factor).where(Factor.id.in_(factor_ids)))
        factors = result.scalars().all()

    if not factors:
        return {"error": "未找到指定因子"}

    # 在线程池中执行 qlib 计算（CPU 密集）
    loop = asyncio.get_running_loop()
    factor_data = await loop.run_in_executor(
        None, _compute_comparison_sync, factors, start, end
    )
    return factor_data


def _compute_one(f, label_df, start: str, end: str) -> dict:
    """计算单个因子的 IC 指标 + 衰减 + IC 时序（供线程池并行调用）。"""
    try:
        try:
            factor_df = load_factor_values(f.expression, start, end)
        except FileNotFoundError as e:
            # AutoML bundle 丢失：跳过该因子但记录错误，避免整体 500
            logger.warning("因子 %s 加载失败（AutoML 模型缺失）: %s", f.name, e)
            return {
                "result": {
                    "id": f.id, "name": f.name, "expression": f.expression,
                    "category": f.category,
                    "error": f"AutoML 模型不可用: {e}",
                },
                "ic_timeseries": [], "decay_data": [],
            }
        ic_metrics = compute_ic(factor_df, label_df)

        # 衰减曲线
        decay = json.loads(f.decay) if f.decay else None
        if not decay:
            try:
                decay = compute_decay(factor_df, label_df, max_lag=10)
            except Exception:
                decay = {}

        result = {
            "id": f.id,
            "name": f.name,
            "expression": f.expression,
            "category": f.category,
            "ic": ic_metrics.get("ic"),
            "rank_ic": ic_metrics.get("rank_ic"),
            "icir": ic_metrics.get("icir"),
            "ir": ic_metrics.get("ir"),
            "n_days": ic_metrics.get("n_days"),
            "decay": decay,
        }

        # 衰减对比数据
        decay_data = [
            {"lag": int(lag), "factor_id": f.id, "ic": ic_val}
            for lag, ic_val in (decay or {}).items()
            if ic_val is not None
        ]

        # IC 时序（每日 IC，向量化计算）
        daily_ic = compute_daily_ic_series(factor_df, label_df)
        ic_timeseries = [
            {"date": str(date.date()), "factor_id": f.id, "ic": round(float(v), 4)}
            for date, v in daily_ic.items()
        ]
        return {"result": result, "ic_timeseries": ic_timeseries, "decay_data": decay_data}
    except Exception as e:
        logger.warning("因子 %s 对比失败: %s", f.name, e)
        return {
            "result": {
                "id": f.id, "name": f.name, "expression": f.expression,
                "error": str(e),
            },
            "ic_timeseries": [], "decay_data": [],
        }


def _compute_comparison_sync(factors, start: str, end: str) -> dict:
    """同步计算因子对比（在线程池中调用）"""
    from app.services.quant.qlib_init import init_qlib
    init_qlib()

    key = (tuple(sorted(f.id for f in factors)), start, end)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    results = []
    decay_data = []
    ic_timeseries = []

    # 标签对所有因子相同，只加载一次（旧实现每因子循环内重复加载 N 次）
    label_df = load_label(start, end)

    # 因子间并行计算（qlib C 扩展释放 GIL，4 并发参照 alpha158 已验证的并发上限）
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_compute_one, f, label_df, start, end) for f in factors]
        for fut in futures:
            out = fut.result()
            results.append(out["result"])
            ic_timeseries.extend(out["ic_timeseries"])
            decay_data.extend(out["decay_data"])

    data = {
        "factors": results,
        "decay_comparison": decay_data,
        "ic_timeseries": ic_timeseries,
        "start": start,
        "end": end,
    }
    _cache_set(key, data)
    return data


async def get_factor_decay(factor_id: int, max_lag: int = 20) -> dict:
    """获取因子 IC 衰减分析（半衰期计算）

    Returns:
        {
            "factor_id": int,
            "decay": {lag: ic},
            "half_life": int,  # IC 衰减到一半所需的期数
            "effective_period": int,  # IC > 0.02 的有效期
        }
    """
    async with async_session() as session:
        f = await session.get(Factor, factor_id)
        if f is None:
            return {"error": "因子不存在"}

    # 优先使用已存储的 decay 数据
    decay = json.loads(f.decay) if f.decay else None

    if not decay:
        # 重新计算
        from app.core.config import settings
        period = settings.quant.get("default_backtest_period", {})
        start = period.get("start", "2020-01-01")
        end = period.get("end", "2024-12-31")

        loop = asyncio.get_running_loop()
        try:
            decay = await loop.run_in_executor(
                None, _compute_decay_sync, f.expression, start, end, max_lag
            )
        except FileNotFoundError as e:
            # AutoML bundle 丢失等不可恢复错误：返回友好错误而非 500
            return {"error": f"AutoML 模型不可用: {e}"}
        except ValueError as e:
            # 文本因子等不支持实时计算的表达式：返回友好错误
            return {"error": str(e)}

        # 更新数据库
        async with async_session() as session:
            r = await session.get(Factor, factor_id)
            if r:
                r.decay = json.dumps(decay) if decay else None
                r.evaluated_at = datetime.now()
                await session.commit()

    # 计算半衰期
    half_life = None
    effective_period = None
    if decay:
        first_ic = decay.get(1, 0)
        if first_ic and first_ic > 0:
            half_threshold = first_ic / 2
            for lag in sorted(decay.keys(), key=int):
                if decay[lag] is not None and decay[lag] < half_threshold:
                    half_life = int(lag)
                    break
        # 有效期：IC > 0.02
        for lag in sorted(decay.keys(), key=int):
            if decay[lag] is not None and abs(decay[lag]) < 0.02:
                effective_period = int(lag) - 1
                break
        if effective_period is None:
            effective_period = max(int(lag) for lag in decay.keys()) if decay else 0

    return {
        "factor_id": factor_id,
        "factor_name": f.name,
        "decay": decay,
        "half_life": half_life,
        "effective_period": effective_period,
    }


def _compute_decay_sync(expr: str, start: str, end: str, max_lag: int) -> dict:
    """同步计算衰减"""
    from app.services.quant.qlib_init import init_qlib
    init_qlib()
    factor_df = load_factor_values(expr, start, end)
    label_df = load_label(start, end)
    return compute_decay(factor_df, label_df, max_lag=max_lag)
