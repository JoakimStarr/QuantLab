"""因子对比与衰减分析服务"""
import json
import logging
import time
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from sqlalchemy import select
from app.core.database import async_session
from app.core.executor import run_io_cpu
from app.models.factor import Factor
from app.services.quant.factor_eval import (
    load_factor_values, load_label, load_close_df, compute_ic, compute_decay, compute_daily_ic_series
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

    # 受管 IO 线程池执行 qlib 计算（CPU/IO 密集，避免占满事件循环）
    factor_data = await run_io_cpu(
        _compute_comparison_sync, factors, start, end
    )
    return factor_data


def _load_daily_ic(expression: str, label_df, start: str, end: str, universe: str = None):
    """加载因子值并计算每日 IC 序列（对比与相关矩阵共用路径）。

    返回 (factor_df, daily_ic)，factor_df 供调用方继续计算 decay 等指标。
    """
    factor_df = load_factor_values(expression, start, end, universe=universe)
    return factor_df, compute_daily_ic_series(factor_df, label_df)


def _compute_one(f, label_df, close_df, start: str, end: str) -> dict:
    """计算单个因子的 IC 指标 + 衰减 + IC 时序（供线程池并行调用）。"""
    try:
        try:
            factor_df, daily_ic = _load_daily_ic(f.expression, label_df, start, end)
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
                decay = compute_decay(factor_df, label_df, max_lag=10,
                                      preloaded_close_df=close_df)
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

        # IC 时序（每日 IC，已在 _load_daily_ic 中向量化计算）
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

    # 标签与价格对所有因子相同，各只加载一次（旧实现每因子循环内重复加载 N 次）
    label_df = load_label(start, end)
    # $close 供 decay 复用；缺失时置 None，compute_decay 回退自行加载
    try:
        close_df = load_close_df(start, end)
    except ValueError:
        close_df = None

    # 因子间并行计算（qlib C 扩展释放 GIL，4 并发参照 alpha158 已验证的并发上限）
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_compute_one, f, label_df, close_df, start, end) for f in factors]
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

        try:
            decay = await run_io_cpu(
                _compute_decay_sync, f.expression, start, end, max_lag
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


# ==================== 因子 IC 相关矩阵 ====================

# 相关矩阵要求的最少重叠交易日数：低于此值认为不可比，矩阵置 NaN（None）
_CORR_MIN_OVERLAP_DAYS = 20


def _build_ic_corr_matrix(ic_series: dict, ids: list, names: list) -> dict:
    """由 {factor_id: 日度IC序列} 构建相关矩阵（纯函数，可离线测试）。

    对齐各因子日度 IC 的公共日期，逐对计算 np.corrcoef；重叠天数不足
    _CORR_MIN_OVERLAP_DAYS 或零方差时置 None，不抛异常。
    """
    n = len(ids)
    matrix = [[None] * n for _ in range(n)]
    overlap = [[0] * n for _ in range(n)]
    ic_summary = {}

    for i, fid in enumerate(ids):
        s = ic_series.get(fid)
        if s is None or len(s) < 2:
            ic_summary[str(fid)] = {"name": names[i], "ic_mean": None,
                                    "icir": None, "n_days": int(len(s)) if s is not None else 0}
            continue
        mean = float(s.mean())
        std = float(s.std())
        ic_summary[str(fid)] = {
            "name": names[i],
            "ic_mean": round(mean, 4),
            "icir": round(mean / std, 4) if std > 1e-12 else None,
            "n_days": int(len(s)),
        }
        matrix[i][i] = 1.0
        overlap[i][i] = int(len(s))

    for i in range(n):
        for j in range(i + 1, n):
            si, sj = ic_series.get(ids[i]), ic_series.get(ids[j])
            if si is None or sj is None:
                continue
            common = si.index.intersection(sj.index)
            a = si.loc[common].astype(float)
            b = sj.loc[common].astype(float)
            mask = a.notna() & b.notna()
            a, b = a[mask], b[mask]
            overlap[i][j] = overlap[j][i] = int(len(a))
            if len(a) >= _CORR_MIN_OVERLAP_DAYS and float(a.std()) > 1e-12 and float(b.std()) > 1e-12:
                c = float(np.corrcoef(a.to_numpy(), b.to_numpy())[0, 1])
                if np.isfinite(c):
                    matrix[i][j] = matrix[j][i] = round(c, 4)

    return {"matrix": matrix, "overlap_counts": overlap, "ic_summary": ic_summary}


async def compute_ic_correlation_matrix(
    factor_ids: list[int], start: str, end: str, universe: str = None
) -> dict:
    """计算多个因子日度 IC 序列的相关矩阵。

    Returns:
        {
            "factor_ids": [id...], "labels": [name...],
            "matrix": n×n（不可比处为 None）,
            "overlap_counts": n×n,
            "ic_summary": {id: {name, ic_mean, icir, n_days}},
            "start", "end",
        }
    """
    # 去重并保持请求顺序
    factor_ids = list(dict.fromkeys(factor_ids))
    async with async_session() as session:
        result = await session.execute(select(Factor).where(Factor.id.in_(factor_ids)))
        found = result.scalars().all()

    if not found:
        return {"error": "未找到指定因子"}

    by_id = {f.id: f for f in found}
    factors = [by_id[i] for i in factor_ids if i in by_id]
    missing = [i for i in factor_ids if i not in by_id]
    if missing:
        logger.warning("相关矩阵计算跳过不存在的因子: %s", missing)

    # 受管 IO 线程池执行（与 compare 同款，避免占满事件循环）
    raw = await run_io_cpu(_compute_ic_correlation_sync, factors, start, end, universe)
    data = dict(raw)  # 缓存对象不直接改写
    data["missing_factor_ids"] = missing
    return data


def _compute_ic_correlation_sync(factors, start: str, end: str, universe: str = None) -> dict:
    """同步计算 IC 相关矩阵（在线程池中调用）。"""
    from app.services.quant.qlib_init import init_qlib
    init_qlib()

    key = ("corr", tuple(sorted(f.id for f in factors)), start, end, universe)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    label_df = load_label(start, end, universe=universe)

    ic_series: dict[int, pd.Series] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            f.id: pool.submit(_load_daily_ic, f.expression, label_df, start, end, universe)
            for f in factors
        }
        for f in factors:
            try:
                _, s = futures[f.id].result()
                ic_series[f.id] = s
            except Exception as e:
                logger.warning("因子 %s IC 序列加载失败: %s", f.name, e)

    ids = [f.id for f in factors]
    names = [f.name for f in factors]
    data = {
        "factor_ids": ids,
        "labels": names,
        **_build_ic_corr_matrix(ic_series, ids, names),
        "start": start,
        "end": end,
    }
    _cache_set(key, data)
    return data
