"""因子对比与衰减分析服务"""
import json
import asyncio
import logging
import numpy as np
import pandas as pd
from datetime import datetime
from sqlalchemy import select
from app.core.database import async_session
from app.models.factor import Factor
from app.services.quant.factor_eval import (
    load_factor_values, load_label, compute_ic, compute_decay
)

logger = logging.getLogger(__name__)


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


def _compute_comparison_sync(factors, start: str, end: str) -> dict:
    """同步计算因子对比（在线程池中调用）"""
    from app.services.quant.qlib_init import init_qlib
    init_qlib()

    results = []
    decay_data = []
    ic_timeseries = []

    for f in factors:
        try:
            try:
                factor_df = load_factor_values(f.expression, start, end)
            except FileNotFoundError as e:
                # AutoML bundle 丢失：跳过该因子但记录错误，避免整体 500
                logger.warning("因子 %s 加载失败（AutoML 模型缺失）: %s", f.name, e)
                results.append({
                    "id": f.id, "name": f.name, "expression": f.expression,
                    "category": f.category,
                    "error": f"AutoML 模型不可用: {e}",
                })
                continue
            label_df = load_label(start, end)
            ic_metrics = compute_ic(factor_df, label_df)

            # 衰减曲线
            decay = json.loads(f.decay) if f.decay else None
            if not decay:
                try:
                    decay = compute_decay(factor_df, label_df, max_lag=10)
                except Exception:
                    decay = {}

            results.append({
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
            })

            # 衰减对比数据
            for lag, ic_val in (decay or {}).items():
                if ic_val is not None:
                    decay_data.append({"lag": int(lag), "factor_id": f.id, "ic": ic_val})

            # IC 时序（每日 IC）
            merged = factor_df.join(label_df, how="inner").dropna()
            if not merged.empty:
                daily_ic = merged.groupby(level="datetime").apply(
                    lambda g: g["factor"].corr(g["label"]) if len(g) >= 2 else np.nan,
                    include_groups=False,
                ).dropna()
                for date, ic_val in daily_ic.items():
                    ic_timeseries.append({
                        "date": str(date.date()),
                        "factor_id": f.id,
                        "ic": round(float(ic_val), 4),
                    })

        except Exception as e:
            logger.warning("因子 %s 对比失败: %s", f.name, e)
            results.append({
                "id": f.id,
                "name": f.name,
                "expression": f.expression,
                "error": str(e),
            })

    return {
        "factors": results,
        "decay_comparison": decay_data,
        "ic_timeseries": ic_timeseries,
        "start": start,
        "end": end,
    }


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
            effective_period = max(int(l) for l in decay.keys()) if decay else 0

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
