"""挖掘候选持久化：mining_candidate 幂等 upsert。

调用点（llm_factor.py）在挖掘不同阶段更新同一候选：
1. LLM 产出后 → upsert(status="generated")
2. 沙箱/去重拒绝后 → upsert(status="rejected", reason=拒绝原因)
3. 评价完成后       → upsert(status="evaluated"/"passed", ic/rank_ic/icir/fail_reasons)

按 (task_id, expression) 唯一，重复调用只刷新字段，不产生重复行。
失败时静默（不阻断挖掘主流程）：候选落库是展示增强，不是必须步骤。
"""
import json
import logging

from sqlalchemy import select

from app.core.database import async_session
from app.models.mining_candidate import MiningCandidate

logger = logging.getLogger(__name__)


async def upsert_candidates(task_id: int, candidates: list[dict], round_no: int = 1) -> None:
    """批量 upsert 候选记录。

    Args:
        task_id: MiningTask.id
        candidates: [{
            name, expression, description,
            status: generated/rejected/evaluated/passed,
            reason: 单条拒绝原因，fail_reasons: 列表,
            ic, rank_ic, icir,
        }]
        round_no: 迭代轮次（非迭代任务恒为 1）
    """
    if not candidates:
        return
    try:
        async with async_session() as session:
            exprs = [c.get("expression") for c in candidates if c.get("expression")]
            existing: dict[str, MiningCandidate] = {}
            if exprs:
                rows = await session.execute(
                    select(MiningCandidate).where(
                        MiningCandidate.task_id == task_id,
                        MiningCandidate.expression.in_(exprs),
                    )
                )
                existing = {r.expression: r for r in rows.scalars().all()}
            for c in candidates:
                expr = c.get("expression")
                if not expr:
                    continue
                row = existing.get(expr)
                if row is None:
                    row = MiningCandidate(task_id=task_id, expression=expr)
                    session.add(row)
                row.round = round_no
                row.name = (c.get("name") or "")[:200]
                row.description = (c.get("description") or "")[:2000]
                row.status = c.get("status", "generated")
                row.reason = (c.get("reason") or "")[:2000]
                fr = c.get("fail_reasons")
                row.fail_reasons = json.dumps(fr, ensure_ascii=False)[:4000] if fr else None
                row.ic = c.get("ic")
                row.rank_ic = c.get("rank_ic")
                row.icir = c.get("icir")
            await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("候选落库失败 task_id=%s: %s", task_id, e)


async def list_candidates(task_id: int) -> list[dict]:
    """查询任务的候选列表（按轮次、id 排序）。"""
    from sqlalchemy import select
    from app.core.database import async_session
    from app.models.mining_candidate import MiningCandidate

    async with async_session() as session:
        rows = (
            await session.execute(
                select(MiningCandidate)
                .where(MiningCandidate.task_id == task_id)
                .order_by(MiningCandidate.round, MiningCandidate.id)
            )
        ).scalars().all()
        return [
            {
                "id": r.id,
                "round": r.round,
                "name": r.name,
                "expression": r.expression,
                "description": r.description,
                "status": r.status,
                "reason": r.reason,
                "fail_reasons": json.loads(r.fail_reasons) if r.fail_reasons else [],
                "ic": r.ic,
                "rank_ic": r.rank_ic,
                "icir": r.icir,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]