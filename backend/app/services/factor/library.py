"""因子库管理：CRUD + 评价调度 + 内置因子种子。"""
import json
import logging
from datetime import datetime
from sqlalchemy import select, func
from app.core.database import async_session
from app.models.factor import Factor
from app.services.factor.expression import validate_expression

logger = logging.getLogger(__name__)


async def list_factors(category: str = None, status: str = "active", sort_by: str = "ic",
                       limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
    """列出因子，支持按 IC 等排序，返回 (items, total)。"""
    async with async_session() as session:
        base_q = select(func.count()).select_from(Factor)
        if category:
            base_q = base_q.where(Factor.category == category)
        if status:
            base_q = base_q.where(Factor.status == status)
        total_result = await session.execute(base_q)
        total = total_result.scalar() or 0

        q = select(Factor)
        if category:
            q = q.where(Factor.category == category)
        if status:
            q = q.where(Factor.status == status)
        sort_col = {"ic": Factor.ic, "rank_ic": Factor.rank_ic, "icir": Factor.icir,
                    "created_at": Factor.created_at}.get(sort_by, Factor.ic)
        q = q.order_by(sort_col.desc().nullslast()).limit(limit).offset(offset)
        result = await session.execute(q)
        rows = result.scalars().all()
    return [_to_dict(r) for r in rows], total


async def get_factor(factor_id: int) -> dict:
    async with async_session() as session:
        r = await session.get(Factor, factor_id)
        if r is None:
            return None
        return _to_dict(r)


async def add_factor(name: str, expression: str, category: str = "builtin",
                     description: str = None, source_task_id: int = None,
                     skip_validation: bool = False) -> dict:
    """新增因子（带表达式安全校验）。"""
    if not skip_validation:
        validate_expression(expression)
    async with async_session() as session:
        factor = Factor(
            name=name, expression=expression, category=category,
            description=description, source_task_id=source_task_id,
        )
        session.add(factor)
        await session.commit()
        await session.refresh(factor)
        return _to_dict(factor)


async def add_factors_batch(factors: list[dict], skip_validation: bool = False) -> list[dict]:
    """批量新增因子，单次 commit。

    Args:
        factors: [{"name":..., "expression":..., "category":..., "description":..., "source_task_id":...}, ...]
        skip_validation: 跳过表达式校验（已由调用方校验过时用）
    Returns:
        新增因子 dict 列表
    """
    if not factors:
        return []
    if not skip_validation:
        for f in factors:
            validate_expression(f["expression"])
    async with async_session() as session:
        objs = []
        for f in factors:
            obj = Factor(
                name=f["name"], expression=f["expression"],
                category=f.get("category", "builtin"),
                description=f.get("description"),
                source_task_id=f.get("source_task_id"),
            )
            objs.append(obj)
        session.add_all(objs)
        await session.commit()
        # expire_on_commit=False，commit 后 id 已由 flush 填充
        return [_to_dict(obj) for obj in objs]


async def disable_factor(factor_id: int) -> bool:
    async with async_session() as session:
        r = await session.get(Factor, factor_id)
        if r is None:
            return False
        r.status = "disabled"
        await session.commit()
        return True


async def update_factor_metrics(factor_id: int, metrics: dict) -> None:
    """更新因子评价指标。"""
    async with async_session() as session:
        r = await session.get(Factor, factor_id)
        if r is None:
            return
        r.ic = metrics.get("ic")
        r.rank_ic = metrics.get("rank_ic")
        r.icir = metrics.get("icir")
        r.ir = metrics.get("ir")
        r.turnover = metrics.get("turnover")
        r.decay = json.dumps(metrics.get("decay")) if metrics.get("decay") else None
        if metrics.get("ic_by_horizon") is not None:
            r.ic_by_horizon = json.dumps(metrics["ic_by_horizon"])
        if metrics.get("orthogonal_ic") is not None:
            r.orthogonal_ic = metrics["orthogonal_ic"]
        r.eval_start = metrics.get("eval_start")
        r.eval_end = metrics.get("eval_end")
        r.evaluated_at = datetime.now()
        await session.commit()


async def evaluate_factor_by_id(factor_id: int, start: str = None, end: str = None) -> dict:
    """对库中因子执行评价（调用 qlib，CPU 密集，应由 worker 调用）。"""
    from app.core.config import settings
    from app.services.quant.factor_eval import evaluate_factor as _eval
    factor = await get_factor(factor_id)
    if factor is None:
        raise ValueError(f"因子 {factor_id} 不存在")
    period = settings.quant.get("default_backtest_period", {})
    start = start or period.get("start", "2020-01-01")
    end = end or period.get("end", "2024-12-31")
    # CPU 密集的因子评价走进程池，避免阻塞事件循环
    from app.core.executor import run_cpu
    horizon = settings.mining.get("llm", {}).get("eval_horizon", 5)
    # 多周期评价：额外评价 1/10/20 天（主 horizon 由 config 决定）
    horizons = [1, 5, 10, 20]
    metrics = await run_cpu(_eval, factor["expression"], start, end,
                            horizon=horizon, horizons=horizons)
    await update_factor_metrics(factor_id, metrics)
    return metrics


def _to_dict(r: Factor) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "expression": r.expression,
        "category": r.category,
        "description": r.description,
        "ic": r.ic,
        "rank_ic": r.rank_ic,
        "icir": r.icir,
        "ir": r.ir,
        "turnover": r.turnover,
        "decay": json.loads(r.decay) if r.decay else None,
        "ic_by_horizon": json.loads(r.ic_by_horizon) if r.ic_by_horizon else None,
        "orthogonal_ic": r.orthogonal_ic,
        "eval_start": r.eval_start,
        "eval_end": r.eval_end,
        "evaluated_at": r.evaluated_at.isoformat() if r.evaluated_at else None,
        "status": r.status,
        "source_task_id": r.source_task_id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
