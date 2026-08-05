"""策略 API：CRUD、回测执行、结果查询。"""
import logging
from fastapi import APIRouter, Query, BackgroundTasks

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


@router.delete("/backtest-results/{result_id}")
async def delete_result_api(result_id: int):
    """软删除回测结果（is_deleted=1），用于清理重复/过期记录。"""
    from app.services.strategy.manager import delete_backtest_result
    ok = await delete_backtest_result(result_id)
    if not ok:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "回测结果不存在", "status": 404})
    return ApiResponse(ok=True, data={"message": f"回测结果 {result_id} 已删除"})


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


async def _run_backtest_task(strategy_id: int, start: str, end: str, backend: str = "qlib",
                             capital: float = None):
    try:
        await run_strategy_backtest(strategy_id, start, end, backend=backend, capital=capital)
        backtest_status.set_completed(strategy_id)
        # 成功回测后把策略状态重置为 active（之前失败写入的 backtest_failed 需清除，
        # 否则一次失败后即使后续回测成功，状态也永远显示失败）
        from app.core.database import async_session
        from app.models.strategy import Strategy
        async with async_session() as session:
            r = await session.get(Strategy, strategy_id)
            if r and r.status != "active":
                r.status = "active"
                # 清除失败标记（保留描述正文）
                if r.description and "[回测失败]" in r.description:
                    r.description = r.description.split("\n[回测失败]")[0].strip()
                await session.commit()
    except Exception as e:
        logger.exception("策略回测失败 strategy_id=%s", strategy_id)
        backtest_status.set_failed(strategy_id, str(e))
        # 更新策略状态为失败
        from app.core.database import async_session
        from app.models.strategy import Strategy
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
    backend: str = Query("qlib", description="回测后端: qlib(默认,工业级A股约束) / vbt(矢量化,快速扫描)"),
    initial_capital: float = Query(None, description="初始资金（元，默认 1 亿，可经 config.quant.initial_capital 配置）"),
):
    """触发策略回测（后台执行）。"""
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装，无法回测", 503)
    # 回测后端白名单：自研 self 已移除（被 qlib 覆盖），仅 qlib / vbt 合法
    if backend not in ("qlib", "vbt"):
        return ApiResponse(ok=False, error={
            "code": "VALIDATION_ERROR",
            "message": f"不支持的回测后端: {backend}（可选: qlib / vbt）",
            "status": 400,
        })
    # 数据同步（回填/补齐/指数/EOD）写 bin 期间，bin 与 day.txt 处于对齐过渡状态，
    # 回测会读到错位数据导致越界/结果消失；fetch-only 任务（只写 PG）不拦截
    from app.services.data.sync_progress import busy_message, writes_bins_active
    if writes_bins_active():
        return ApiResponse(ok=False, error={
            "code": "SYNC_IN_PROGRESS",
            "message": busy_message() + "；回测读取 qlib bin，数据同步写 bin 期间结果不可靠，请稍后重试",
            "status": 409,
        })
    strategy = await get_strategy(strategy_id)
    if strategy is None:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "策略不存在", "status": 404})
    backtest_status.set_running(strategy_id)
    background_tasks.add_task(_run_backtest_task, strategy_id, start_date, end_date, backend, initial_capital)
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
