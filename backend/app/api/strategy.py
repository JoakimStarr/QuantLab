"""策略 API：CRUD、回测执行、结果查询。"""
import logging
from fastapi import APIRouter, Depends, Query, BackgroundTasks

from app.core.errors import AppError
from app.schemas.common import ApiResponse
from app.services.strategy.manager import (
    list_strategies, get_strategy, create_strategy, archive_strategy,
    run_strategy_backtest, list_backtest_results, get_backtest_result,
)
from app.services.strategy import backtest_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/strategies", tags=["strategy"])


@router.get("")
async def list_strategies_api(status: str = Query("active")):
    items, total = await list_strategies(status=status)
    return ApiResponse(ok=True, data={"items": items, "total": total})


@router.post("")
async def create_strategy_api(
    name: str = Query(...),
    factor_ids: list[int] = Query(...),
    combination_method: str = Query("equal_weight"),
    topk: int = Query(None),
    n_drop: int = Query(None),
    rebalance_freq: str = Query("day"),
    benchmark: str = Query(None),
    description: str = Query(None),
    orthogonalize: int = Query(0, description="是否启用因子正交化 0/1"),
):
    if not factor_ids:
        raise AppError("VALIDATION_ERROR", "至少选择一个因子", 422)
    item = await create_strategy(
        name=name, factor_ids=factor_ids, combination_method=combination_method,
        topk=topk, n_drop=n_drop, rebalance_freq=rebalance_freq,
        benchmark=benchmark, description=description, orthogonalize=orthogonalize,
    )
    return ApiResponse(ok=True, data=item)


# 注意：字面量路由必须放在 {strategy_id} 参数路由之前，
# 否则 /strategies/backtest-results 会被 /{strategy_id} 优先匹配并报 422。
@router.get("/backtest-results/{result_id}")
async def get_result_api(result_id: int):
    item = await get_backtest_result(result_id)
    if item is None:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "回测结果不存在", "status": 404})
    return ApiResponse(ok=True, data=item)


@router.get("/backtest-results")
async def list_all_results_api(limit: int = Query(20, le=100)):
    """全局最近回测结果（不限定策略）。"""
    items = await list_backtest_results(strategy_id=None, limit=limit)
    return ApiResponse(ok=True, data={"items": items, "total": len(items)})


@router.get("/{strategy_id}")
async def get_strategy_api(strategy_id: int):
    item = await get_strategy(strategy_id)
    if item is None:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "策略不存在", "status": 404})
    return ApiResponse(ok=True, data=item)


@router.delete("/{strategy_id}")
async def archive_strategy_api(strategy_id: int):
    ok = await archive_strategy(strategy_id)
    if not ok:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "策略不存在", "status": 404})
    return ApiResponse(ok=True, data={"id": strategy_id, "status": "archived"})


async def _run_backtest_task(strategy_id: int, start: str, end: str, backend: str = "qlib"):
    try:
        await run_strategy_backtest(strategy_id, start, end, backend=backend)
        backtest_status.set_completed(strategy_id)
    except Exception as e:
        logger.exception("策略回测失败 strategy_id=%s", strategy_id)
        backtest_status.set_failed(strategy_id, str(e))
        # 更新策略状态为失败
        from app.core.database import async_session
        from app.models.strategy import Strategy
        from sqlalchemy import select
        async with async_session() as session:
            r = await session.get(Strategy, strategy_id)
            if r:
                r.status = "backtest_failed"
                r.description = (r.description or "") + f"\n[回测失败] {str(e)[:200]}"
                await session.commit()


@router.post("/{strategy_id}/backtest")
async def run_backtest_api(
    strategy_id: int,
    background_tasks: BackgroundTasks,
    start_date: str = Query(None),
    end_date: str = Query(None),
    backend: str = Query("qlib", description="回测后端: qlib(默认,工业级) / self(自研)"),
):
    """触发策略回测（后台执行）。"""
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装，无法回测", 503)
    strategy = await get_strategy(strategy_id)
    if strategy is None:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "策略不存在", "status": 404})
    backtest_status.set_running(strategy_id)
    background_tasks.add_task(_run_backtest_task, strategy_id, start_date, end_date, backend)
    return ApiResponse(ok=True, data={
        "message": f"策略 {strategy_id} 回测已提交（后台执行）",
        "strategy_id": strategy_id,
    })


@router.get("/{strategy_id}/backtest-status")
async def get_backtest_status_api(strategy_id: int):
    """获取策略回测状态。"""
    status = backtest_status.get_status(strategy_id)
    return ApiResponse(ok=True, data={"strategy_id": strategy_id, **status})


@router.get("/{strategy_id}/backtest-results")
async def list_results_api(strategy_id: int, limit: int = Query(20, le=100)):
    items = await list_backtest_results(strategy_id=strategy_id, limit=limit)
    return ApiResponse(ok=True, data={"items": items, "total": len(items)})
