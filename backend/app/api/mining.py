"""AI 因子挖掘 API：LLM/符号回归挖掘任务管理。"""
import json
import logging
import asyncio
from datetime import datetime
from fastapi import APIRouter, Depends, Query, BackgroundTasks, Request
from sqlalchemy import select, func

from app.core.database import get_db, async_session
from app.core.errors import AppError
from app.core.config import settings
from app.core.ratelimit import limiter
from app.models.mining_task import MiningTask
from app.schemas.common import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mining", tags=["mining"])

# 全局并发信号量（懒初始化，读取 task.max_concurrent）
_mining_sem: asyncio.Semaphore | None = None


def _get_mining_sem() -> asyncio.Semaphore:
    """获取（或首次创建）挖掘任务并发信号量。"""
    global _mining_sem
    if _mining_sem is None:
        max_concurrent = int((settings.task or {}).get("max_concurrent", 2))
        _mining_sem = asyncio.Semaphore(max_concurrent)
        logger.info("挖掘任务并发上限: %d", max_concurrent)
    return _mining_sem


def _task_timeout(task_type: str = None) -> int:
    """挖掘任务超时秒数，按类型分级。

    Args:
        task_type: llm/symbolic/text/automl/optimize；None 用默认 task_timeout_seconds
    """
    task_cfg = settings.task or {}
    timeouts = task_cfg.get("timeouts", {}) or {}
    if task_type and task_type in timeouts:
        return int(timeouts[task_type])
    return int(task_cfg.get("task_timeout_seconds", 300))


async def _safe_run_task(task_id: int, coro_factory, label: str, task_type: str = None, timeout: int = None) -> None:
    """统一的挖掘任务执行包装器。

    - 用 asyncio.wait_for 强制超时，超时即标记 failed
    - 兜底捕获所有异常并更新状态为 failed（防止 BackgroundTasks 静默吞异常）
    - 保证 task 状态不会卡在 running
    - task_type 指定任务类型，用于按类型分级超时
    - timeout 显式指定超时秒数，优先于 task_type（供动态超时场景使用）
    """
    if timeout is None:
        timeout = _task_timeout(task_type)
    sem = _get_mining_sem()
    try:
        # 信号量限流：超出 max_concurrent 的任务在此排队，超时只计实际执行时间
        async with sem:
            await asyncio.wait_for(coro_factory(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.error("%s 任务超时 task_id=%s (timeout=%ss)", label, task_id, timeout)
        await _mark_failed(task_id, f"任务超时 (timeout={timeout}s)")
    except Exception as e:
        logger.exception("%s 任务失败 task_id=%s", label, task_id)
        await _mark_failed(task_id, str(e)[:500])


async def _mark_failed(task_id: int, error: str) -> None:
    """将挖掘任务标记为 failed（兜底，避免状态卡 running）。"""
    try:
        from app.services.mining.task_utils import update_task_status
        await update_task_status(task_id, status="failed", error=error,
                                 finished_at=datetime.now())
    except Exception:
        logger.exception("标记挖掘任务 failed 失败 task_id=%s", task_id)


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
    from app.services.mining.llm_factor import mine_with_llm
    await _safe_run_task(task_id, lambda: mine_with_llm(task_id, n), "LLM 挖掘", "llm")


async def _run_llm_iterative_task(task_id: int, n_rounds: int, n: int):
    """LLM 迭代挖掘任务执行器（n_rounds > 1 时使用）。

    动态超时：基础 + 每轮增量，避免多轮挖掘共用单轮超时。
    """
    from app.services.mining.llm_factor import mine_with_llm_iterative
    task_cfg = settings.task or {}
    timeouts = task_cfg.get("timeouts", {}) or {}
    base = int(timeouts.get("llm_iterative", 600))
    per_round = int(timeouts.get("llm_iterative_per_round", 180))
    timeout = base + (n_rounds - 1) * per_round
    await _safe_run_task(
        task_id,
        lambda: mine_with_llm_iterative(task_id, n_rounds=n_rounds, n_candidates=n),
        "LLM 迭代挖掘", timeout=timeout,
    )


@router.post("/llm")
@limiter.limit("3/minute")
async def mine_llm_api(
    request: Request,
    background_tasks: BackgroundTasks,
    n_candidates: int = Query(None),
    n_rounds: int = Query(1, ge=1, le=5, description="迭代轮数（>1 启用迭代挖掘）"),
):
    """启动 LLM 因子挖掘（后台执行）。

    n_rounds > 1 时启用迭代挖掘：每轮生成→校验→IC评价→反馈给 LLM 逐轮改进。
    """
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装，挖掘需要 IC 评价", 503)
    n = n_candidates or settings.mining.get("llm", {}).get("candidates_per_run", 10)
    task_id = await _create_task("llm", {"n_candidates": n, "n_rounds": n_rounds})
    if n_rounds and n_rounds > 1:
        background_tasks.add_task(_run_llm_iterative_task, task_id, n_rounds, n)
        return ApiResponse(ok=True, data={
            "task_id": task_id, "type": "llm", "status": "pending", "n_rounds": n_rounds,
            "message": f"LLM 迭代因子挖掘已提交（{n_rounds} 轮，后台执行）",
        })
    background_tasks.add_task(_run_llm_task, task_id, n)
    return ApiResponse(ok=True, data={"task_id": task_id, "type": "llm", "status": "pending",
                                       "message": "LLM 因子挖掘已提交（后台执行）"})


async def _run_symbolic_task(task_id: int):
    from app.services.mining.symbolic import mine_with_symbolic
    await _safe_run_task(task_id, lambda: mine_with_symbolic(task_id), "符号回归挖掘", "symbolic")


@router.post("/symbolic")
@limiter.limit("3/minute")
async def mine_symbolic_api(request: Request, background_tasks: BackgroundTasks):
    """启动符号回归因子挖掘（后台执行）。"""
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装，挖掘需要 IC 评价", 503)
    task_id = await _create_task("symbolic", settings.mining.get("symbolic", {}))
    background_tasks.add_task(_run_symbolic_task, task_id)
    return ApiResponse(ok=True, data={"task_id": task_id, "type": "symbolic", "status": "pending",
                                       "message": "符号回归因子挖掘已提交（后台执行）"})


async def _run_automl_task(task_id: int, factor_ids: list[int], method: str):
    from app.services.mining.automl import mine_with_automl
    await _safe_run_task(
        task_id,
        lambda: mine_with_automl(task_id, factor_ids, method),
        "AutoML 组合", "automl",
    )


@router.post("/automl")
@limiter.limit("3/minute")
async def mine_automl_api(
    request: Request,
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
    from app.services.mining.text_factor import mine_with_text
    await _safe_run_task(task_id, lambda: mine_with_text(task_id, codes), "文本因子挖掘", "text")


@router.post("/text")
@limiter.limit("3/minute")
async def mine_text_api(
    request: Request,
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
