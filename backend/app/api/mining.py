"""AI 因子挖掘 API：LLM/符号回归挖掘任务管理。"""
import json
import logging
from fastapi import APIRouter, Depends, Query, BackgroundTasks
from sqlalchemy import select, func

from app.core.database import get_db, async_session
from app.core.errors import AppError
from app.core.config import settings
from app.models.mining_task import MiningTask
from app.schemas.common import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mining", tags=["mining"])


def _task_dict(r: MiningTask) -> dict:
    return {
        "id": r.id, "type": r.type, "status": r.status,
        "params": json.loads(r.params) if r.params else None,
        "candidates_generated": r.candidates_generated,
        "candidates_passed": r.candidates_passed,
        "best_ic": r.best_ic,
        "result_factor_ids": json.loads(r.result_factor_ids) if r.result_factor_ids else [],
        "error": r.error,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


async def _create_task(task_type: str, params: dict) -> int:
    async with async_session() as session:
        t = MiningTask(type=task_type, status="pending", params=json.dumps(params))
        session.add(t)
        await session.commit()
        await session.refresh(t)
        return t.id


@router.get("/tasks")
async def list_tasks_api(
    task_type: str = Query(None),
    status: str = Query(None),
    limit: int = Query(50, le=200),
    db=Depends(get_db),
):
    q = select(MiningTask).order_by(MiningTask.created_at.desc()).limit(limit)
    if task_type:
        q = q.where(MiningTask.type == task_type)
    if status:
        q = q.where(MiningTask.status == status)
    # 总数查询
    count_q = select(func.count()).select_from(MiningTask)
    if task_type:
        count_q = count_q.where(MiningTask.type == task_type)
    if status:
        count_q = count_q.where(MiningTask.status == status)
    count_result = await db.execute(count_q)
    total = count_result.scalar() or 0
    result = await db.execute(q)
    items = [_task_dict(r) for r in result.scalars().all()]
    return ApiResponse(ok=True, data={"items": items, "total": total})


@router.get("/tasks/{task_id}")
async def get_task_api(task_id: int, db=Depends(get_db)):
    r = await db.get(MiningTask, task_id)
    if r is None:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "任务不存在", "status": 404})
    return ApiResponse(ok=True, data=_task_dict(r))


async def _run_llm_task(task_id: int, n: int):
    try:
        from app.services.mining.llm_factor import mine_with_llm
        await mine_with_llm(task_id, n)
    except Exception:
        logger.exception("LLM 挖掘任务失败 task_id=%s", task_id)


@router.post("/llm")
async def mine_llm_api(
    background_tasks: BackgroundTasks,
    n_candidates: int = Query(None),
):
    """启动 LLM 因子挖掘（后台执行）。"""
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装，挖掘需要 IC 评价", 503)
    n = n_candidates or settings.mining.get("llm", {}).get("candidates_per_run", 10)
    task_id = await _create_task("llm", {"n_candidates": n})
    background_tasks.add_task(_run_llm_task, task_id, n)
    return ApiResponse(ok=True, data={"task_id": task_id, "type": "llm", "status": "pending",
                                       "message": "LLM 因子挖掘已提交（后台执行）"})


async def _run_symbolic_task(task_id: int):
    try:
        from app.services.mining.symbolic import mine_with_symbolic
        await mine_with_symbolic(task_id)
    except Exception:
        logger.exception("符号回归挖掘任务失败 task_id=%s", task_id)


@router.post("/symbolic")
async def mine_symbolic_api(background_tasks: BackgroundTasks):
    """启动符号回归因子挖掘（后台执行）。"""
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装，挖掘需要 IC 评价", 503)
    task_id = await _create_task("symbolic", settings.mining.get("symbolic", {}))
    background_tasks.add_task(_run_symbolic_task, task_id)
    return ApiResponse(ok=True, data={"task_id": task_id, "type": "symbolic", "status": "pending",
                                       "message": "符号回归因子挖掘已提交（后台执行）"})


async def _run_automl_task(task_id: int, factor_ids: list[int], method: str):
    try:
        from app.services.mining.automl import mine_with_automl
        await mine_with_automl(task_id, factor_ids, method)
    except Exception:
        logger.exception("AutoML 组合任务失败 task_id=%s", task_id)


@router.post("/automl")
async def mine_automl_api(
    background_tasks: BackgroundTasks,
    factor_ids: list[int] = Query(..., description="参与组合的因子 id 列表"),
    method: str = Query(None, description="lightgbm/linear"),
):
    """启动 AutoML 因子组合（后台执行）。"""
    if not factor_ids:
        raise AppError("VALIDATION_ERROR", "至少选择一个因子", 422)
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装，组合需要数据", 503)
    task_id = await _create_task("automl", {"factor_ids": factor_ids, "method": method})
    background_tasks.add_task(_run_automl_task, task_id, factor_ids, method)
    return ApiResponse(ok=True, data={"task_id": task_id, "type": "automl", "status": "pending",
                                       "message": "AutoML 因子组合已提交（后台执行）"})


async def _run_text_task(task_id: int, codes: list[str]):
    try:
        from app.services.mining.text_factor import mine_with_text
        await mine_with_text(task_id, codes)
    except Exception:
        logger.exception("文本因子挖掘任务失败 task_id=%s", task_id)


@router.post("/text")
async def mine_text_api(
    background_tasks: BackgroundTasks,
    codes: list[str] = Query(None, description="指定股票代码，默认用 universe 前30"),
):
    """启动文本因子挖掘（后台执行）。"""
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装，挖掘需要 IC 评价", 503)
    task_id = await _create_task("text", {"codes": codes})
    background_tasks.add_task(_run_text_task, task_id, codes)
    return ApiResponse(ok=True, data={"task_id": task_id, "type": "text", "status": "pending",
                                       "message": "文本因子挖掘已提交（后台执行）"})
