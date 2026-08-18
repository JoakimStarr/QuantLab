"""AI 因子挖掘 API：LLM/符号回归挖掘任务管理。

挖掘任务通过独立子进程执行（app.services.mining.mining_worker）：
- 长任务不占 web 事件循环，uvicorn --reload 重启不会卡死/丢任务
- 并发上限由 DB 中 running+pending 任务数控制（max_concurrent），
  进程内信号量在子进程模型下无法跨进程限流，改为提交前 DB 计数检查。
"""
import json
import logging

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import async_session, get_db
from app.core.errors import AppError
from app.core.ratelimit import limiter
from app.models.mining_task import MiningTask
from app.schemas.common import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mining", tags=["mining"])


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


async def _ensure_mining_capacity() -> str | None:
    """检查挖掘并发上限（子进程模型下用 DB 计数替代进程内信号量）。

    running + pending 任务数 >= max_concurrent 时拒绝新提交，返回提示信息。
    pending 也计入是因为 worker 子进程启动存在窗口期（先创建任务、后置 running）。
    """
    max_concurrent = int((settings.task or {}).get("max_concurrent", 2))
    async with async_session() as session:
        result = await session.execute(
            select(func.count())
            .select_from(MiningTask)
            .where(MiningTask.status.in_(["running", "pending"]))
        )
        active = result.scalar() or 0
    if active >= max_concurrent:
        return f"已有 {active} 个挖掘任务在运行（上限 {max_concurrent}），请等待完成后重试"
    return None


async def _ensure_sync_idle() -> str | None:
    """挖掘任务需要读取 qlib bin；仅当活跃任务会"重塑日历对齐"（回填历史扩展/
    补齐重建）时才拒绝，避免读到错位数据。EOD/ETF/指数等纯追加同步写 bin 为
    原子写，挖掘可并发执行（数据同步与挖掘解耦）。"""
    from app.services.data.sync_progress import busy_message, calendar_shifting_active

    if not calendar_shifting_active():
        return None
    return busy_message() + "；挖掘需要读取 qlib bin 数据，回填/补齐会重塑日历，期间数据不稳定，请稍后重试"


def _spawn(task_id: int, task_type: str, params: dict) -> None:
    """启动独立挖掘子进程（不阻塞事件循环）。"""
    from app.services.mining.mining_worker import spawn_mining_worker
    spawn_mining_worker(task_id, task_type, params)


def _task_dict(r: MiningTask) -> dict:
    result = json.loads(r.result) if r.result else None
    return {
        "id": r.id, "type": r.type, "status": r.status,
        "params": json.loads(r.params) if r.params else None,
        "candidates_generated": r.candidates_generated,
        "candidates_passed": r.candidates_passed,
        "best_ic": r.best_ic,
        "result_factor_ids": json.loads(r.result_factor_ids) if r.result_factor_ids else [],
        "improvement_curve": result.get("improvement_curve") if result else None,
        "stopped_early": result.get("stopped_early") if result else None,
        "stop_reason": result.get("stop_reason") if result else None,
        "error": r.error,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


async def _create_task(task_type: str, params: dict) -> int:
    from app.core.audit_log import audit
    from app.core.metrics import mining_tasks_total

    async with async_session() as session:
        t = MiningTask(type=task_type, status="pending", params=json.dumps(params))
        session.add(t)
        await session.commit()
        await session.refresh(t)
        audit(
            "mining_submit",
            resource=f"task:{t.id}",
            detail=f"提交 {task_type} 挖掘任务",
            task_type=task_type,
            params=params,
        )
        mining_tasks_total.labels(type=task_type, status="pending").inc()
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


@router.get("/tasks/{task_id}/candidates")
async def get_task_candidates_api(task_id: int, db=Depends(get_db)):
    """查询任务挖掘出的候选因子（含未通过的，按轮次排序）。

    挖掘过程的候选（含沙箱拒绝/评价未过的原因）都会记录在 mining_candidate 表，
    供复盘"挖过什么、被哪一关拒绝"。
    """
    r = await db.get(MiningTask, task_id)
    if r is None:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "任务不存在", "status": 404})
    from app.services.mining.candidate_store import list_candidates
    items = await list_candidates(task_id)
    return ApiResponse(ok=True, data={"items": items, "total": len(items)})


async def _run_llm_task(task_id: int, n: int, universe: str = None):
    """LLM 挖掘任务执行器（独立子进程）。"""
    _spawn(task_id, "llm", {"n_candidates": n, "n_rounds": 1, "universe": universe})


async def _run_llm_iterative_task(task_id: int, n_rounds: int, n: int, universe: str = None):
    """LLM 迭代挖掘任务执行器（n_rounds > 1 时使用，独立子进程）。"""
    _spawn(task_id, "llm", {"n_rounds": n_rounds, "n_candidates": n, "universe": universe})


@router.post("/llm")
@limiter.limit("3/minute")
async def mine_llm_api(
    request: Request,
    n_candidates: int = Query(None),
    n_rounds: int = Query(1, ge=1, le=5, description="迭代轮数（>1 启用迭代挖掘）"),
    universe: str = Query(None, description="标的池 csi300/csi500/all/etf_all"),
):
    """启动 LLM 因子挖掘（后台执行）。

    n_rounds > 1 时启用迭代挖掘：每轮生成→校验→IC评价→反馈给 LLM 逐轮改进。
    """
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装，挖掘需要 IC 评价", 503)
    busy = await _ensure_sync_idle()
    if busy:
        return ApiResponse(ok=False, error={"code": "SYNC_IN_PROGRESS", "message": busy, "status": 409})
    capacity = await _ensure_mining_capacity()
    if capacity:
        return ApiResponse(ok=False, error={"code": "SYNC_IN_PROGRESS", "message": capacity, "status": 409})
    n = n_candidates or settings.mining.get("llm", {}).get("candidates_per_run", 10)
    task_id = await _create_task("llm", {"n_candidates": n, "n_rounds": n_rounds,
                                         "universe": universe})
    if n_rounds and n_rounds > 1:
        await _run_llm_iterative_task(task_id, n_rounds, n, universe)
        return ApiResponse(ok=True, data={
            "task_id": task_id, "type": "llm", "status": "pending", "n_rounds": n_rounds,
            "message": f"LLM 迭代因子挖掘已提交（{n_rounds} 轮，后台执行）",
        })
    await _run_llm_task(task_id, n, universe)
    return ApiResponse(ok=True, data={"task_id": task_id, "type": "llm", "status": "pending",
                                      "message": "LLM 因子挖掘已提交（后台执行）"})


async def _run_symbolic_task(task_id: int, universe: str = None):
    _spawn(task_id, "symbolic", {"universe": universe})


@router.post("/symbolic")
@limiter.limit("3/minute")
async def mine_symbolic_api(request: Request,
                            universe: str = Query(None, description="标的池 csi300/csi500/all/etf_all")):
    """启动符号回归因子挖掘（后台执行）。"""
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装，挖掘需要 IC 评价", 503)
    busy = await _ensure_sync_idle()
    if busy:
        return ApiResponse(ok=False, error={"code": "SYNC_IN_PROGRESS", "message": busy, "status": 409})
    capacity = await _ensure_mining_capacity()
    if capacity:
        return ApiResponse(ok=False, error={"code": "SYNC_IN_PROGRESS", "message": capacity, "status": 409})
    params = dict(settings.mining.get("symbolic", {}))
    params["universe"] = universe
    task_id = await _create_task("symbolic", params)
    await _run_symbolic_task(task_id, universe)
    return ApiResponse(ok=True, data={"task_id": task_id, "type": "symbolic", "status": "pending",
                                      "message": "符号回归因子挖掘已提交（后台执行）"})


async def _run_automl_task(task_id: int, factor_ids: list[int], method: str,
                           walk_forward: bool = False, universe: str = None):
    _spawn(task_id, "automl", {"factor_ids": factor_ids, "method": method,
                               "walk_forward": walk_forward, "universe": universe})


@router.post("/automl")
@limiter.limit("3/minute")
async def mine_automl_api(
    request: Request,
    factor_ids: list[int] = Query(..., description="参与组合的因子 id 列表"),
    method: str = Query(None, description="lightgbm/linear"),
    walk_forward: int = Query(0, description="是否使用 Walk-Forward 滚动重训 0/1"),
    universe: str = Query(None, description="标的池 csi300/csi500/all/etf_all"),
):
    """启动 AutoML 因子组合（后台执行）。"""
    if not factor_ids:
        raise AppError("VALIDATION_ERROR", "至少选择一个因子", 422)
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装，组合需要数据", 503)
    busy = await _ensure_sync_idle()
    if busy:
        return ApiResponse(ok=False, error={"code": "SYNC_IN_PROGRESS", "message": busy, "status": 409})
    capacity = await _ensure_mining_capacity()
    if capacity:
        return ApiResponse(ok=False, error={"code": "SYNC_IN_PROGRESS", "message": capacity, "status": 409})
    task_id = await _create_task("automl", {"factor_ids": factor_ids, "method": method,
                                            "walk_forward": bool(walk_forward),
                                            "universe": universe})
    await _run_automl_task(task_id, factor_ids, method, bool(walk_forward), universe)
    return ApiResponse(ok=True, data={"task_id": task_id, "type": "automl", "status": "pending",
                                      "message": f"AutoML 因子组合已提交（{('Walk-Forward ' if walk_forward else '')}后台执行）"})


async def _run_text_task(task_id: int, codes: list[str]):
    _spawn(task_id, "text", {"codes": codes})


@router.post("/text")
@limiter.limit("3/minute")
async def mine_text_api(
    request: Request,
    codes: list[str] = Query(None, description="指定股票代码，默认用 universe 前30"),
):
    """启动文本因子挖掘（后台执行）。"""
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装，挖掘需要 IC 评价", 503)
    busy = await _ensure_sync_idle()
    if busy:
        return ApiResponse(ok=False, error={"code": "SYNC_IN_PROGRESS", "message": busy, "status": 409})
    capacity = await _ensure_mining_capacity()
    if capacity:
        return ApiResponse(ok=False, error={"code": "SYNC_IN_PROGRESS", "message": capacity, "status": 409})
    task_id = await _create_task("text", {"codes": codes})
    await _run_text_task(task_id, codes)
    return ApiResponse(ok=True, data={"task_id": task_id, "type": "text", "status": "pending",
                                      "message": "文本因子挖掘已提交（后台执行）"})
