"""因子评价 job 服务：创建/查询/取消 + DB 状态管理。"""
import json
import logging
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import defer

from app.core.database import async_session
from app.models.factor_eval_job import FactorEvalJob

logger = logging.getLogger(__name__)

# factor_eval worker 并发上限（与挖掘的 max_concurrent 同级语义）
_MAX_CONCURRENT = 2


def _job_dict(r: FactorEvalJob, full: bool = False) -> dict:
    """job 摘要/详情 dict。列表省略 result 大列。"""
    base = {
        "id": r.id,
        "kind": r.kind,
        "factor_ids": json.loads(r.factor_ids) if r.factor_ids else [],
        "start_date": r.start_date,
        "end_date": r.end_date,
        "universe": r.universe,
        "status": r.status,
        "total": r.total,
        "done": r.done,
        "current_label": r.current_label,
        "error": r.error,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
    if full:
        base["result"] = json.loads(r.result) if r.result else None
    return base


async def ensure_eval_capacity() -> str | None:
    """running+pending 的 factor_eval job 数超限时返回拒绝提示。"""
    async with async_session() as session:
        result = await session.execute(
            select(func.count())
            .select_from(FactorEvalJob)
            .where(FactorEvalJob.status.in_(["pending", "running"]),
                   FactorEvalJob.is_deleted == 0)
        )
        active = result.scalar() or 0
    if active >= _MAX_CONCURRENT:
        return f"已有 {active} 个因子评价任务在运行（上限 {_MAX_CONCURRENT}），请等待完成后重试"
    return None


async def create_eval_job(kind: str, factor_ids: list[int], start: str = None,
                          end: str = None, universe: str = None) -> dict:
    """创建评价 job 并落库，返回 job dict（不负责 spawn）。"""
    async with async_session() as session:
        job = FactorEvalJob(
            kind=kind,
            factor_ids=json.dumps(factor_ids),
            start_date=start,
            end_date=end,
            universe=universe,
            status="pending",
            total=len(factor_ids),
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return _job_dict(job)


async def list_eval_jobs(limit: int = 20) -> tuple[list[dict], int]:
    async with async_session() as session:
        total = (await session.execute(
            select(func.count()).select_from(FactorEvalJob).where(FactorEvalJob.is_deleted == 0)
        )).scalar() or 0
        result = await session.execute(
            select(FactorEvalJob)
            .where(FactorEvalJob.is_deleted == 0)
            .options(defer(FactorEvalJob.result))
            .order_by(FactorEvalJob.created_at.desc())
            .limit(limit)
        )
        rows = result.scalars().all()
        return [_job_dict(r) for r in rows], total


async def get_eval_job(job_id: int) -> dict | None:
    async with async_session() as session:
        r = await session.get(FactorEvalJob, job_id)
        if r is None or r.is_deleted:
            return None
        return _job_dict(r, full=True)


async def cancel_eval_job(job_id: int) -> bool:
    """软取消：置 cancelled，worker 在下一个因子检查点退出。"""
    async with async_session() as session:
        r = await session.get(FactorEvalJob, job_id)
        if r is None or r.is_deleted:
            return False
        if r.status in ("done", "failed", "cancelled"):
            return True  # 已终态，无需处理
        r.status = "cancelled"
        if r.finished_at is None:
            r.finished_at = datetime.now()
        await session.commit()
        return True


async def recover_stale_eval_jobs() -> None:
    """启动时恢复卡死评价 job：running 且 worker 已死 → failed；pending 且 worker
    存活 → 保持（由 spawn 兜底重新投递场景很少，此处仅做 running 兜底）。"""
    from app.services.factor.factor_eval_worker import is_eval_worker_alive

    now = datetime.now()
    recovered = []
    async with async_session() as session:
        result = await session.execute(
            select(FactorEvalJob).where(FactorEvalJob.status == "running")
        )
        for job in result.scalars().all():
            if is_eval_worker_alive(job.id):
                logger.info("recover eval: job_id=%s worker 仍存活，跳过", job.id)
                continue
            job.status = "failed"
            job.error = "container restart interrupted eval job (zombie recovered)"
            job.finished_at = now
            recovered.append(job.id)
        await session.commit()
    if not recovered:
        logger.info("recover eval: 无卡死评价任务")
    else:
        logger.info("recover eval: 共恢复 %d 个卡死评价任务: %s", len(recovered), recovered)
