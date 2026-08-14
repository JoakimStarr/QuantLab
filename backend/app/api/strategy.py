"""策略 API：CRUD、回测执行、结果查询。"""
import logging
from datetime import datetime

from fastapi import APIRouter, Query

from app.core.errors import AppError
from app.schemas.common import ApiResponse
from app.services.strategy import backtest_status
from app.services.strategy.manager import (
    archive_strategy,
    create_strategy,
    get_backtest_result,
    get_strategy,
    list_backtest_results,
    list_strategies,
)

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


@router.post("/{strategy_id}/backtest")
async def run_backtest_api(
    strategy_id: int,
    start_date: str = Query(None),
    end_date: str = Query(None),
    backend: str = Query("qlib", description="回测后端: qlib(默认,工业级A股约束) / vbt(矢量化,快速扫描)"),
    initial_capital: float = Query(None, description="初始资金（元，默认 1 亿，可经 config.quant.initial_capital 配置）"),
    trade_unit: int = Query(None, ge=1, description="A股整手大小: 100=整手约束(默认qlib内置), 1=关闭整手允许小数股"),
    deal_price: str = Query(None, description="成交价: close=T+1收盘(默认) / open=T+1开盘(更保守)"),
    slippage_bps: float = Query(None, ge=0, description="滑点(基点)，默认 0"),
    cost_buy: float = Query(None, ge=0, description="买入费率(小数)，默认 config 0.0013"),
    cost_sell: float = Query(None, ge=0, description="卖出费率(小数)，默认 config 0.0023"),
    min_cost: float = Query(None, ge=0, description="单笔最低佣金(元)，默认 5"),
    universe: str = Query(None, description="标的池 csi300/csi500/all/etf_all"),
    asset_class: str = Query("stock", description="标的类别: stock=A股(T+1/整手/涨跌停) / etf=ETF(T+0语义/无整手/涨跌停放宽)"),
):
    """触发策略回测（独立 worker 子进程执行，不占 web 进程）。"""
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
    # 标的类别白名单
    if asset_class not in ("stock", "etf"):
        return ApiResponse(ok=False, error={
            "code": "VALIDATION_ERROR",
            "message": f"不支持的标的类别: {asset_class}（可选: stock / etf）",
            "status": 400,
        })
    # 数据同步与回测解耦：只有"会重塑日历对齐"的同步（回填历史扩展/补齐重建）
    # 会读到错位 bin，需等待；EOD/ETF/指数等纯追加同步写 bin 是原子写，
    # 回测可并发执行、互不打扰。
    from app.services.data.sync_progress import busy_message, calendar_shifting_active
    if calendar_shifting_active():
        return ApiResponse(ok=False, error={
            "code": "SYNC_IN_PROGRESS",
            "message": busy_message() + "；回填/补齐会重塑日历，期间回测结果不可靠，请稍后重试",
            "status": 409,
        })
    strategy = await get_strategy(strategy_id)
    if strategy is None:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "策略不存在", "status": 404})
    from app.services.strategy.strategy_worker import is_task_running, spawn_strategy_worker
    if is_task_running("backtest", strategy_id):
        return ApiResponse(ok=True, data={
            "message": f"策略 {strategy_id} 回测正在执行中，请勿重复提交",
            "strategy_id": strategy_id, "running": True,
        })
    backtest_status.set_running(strategy_id)
    from app.core.audit_log import audit

    audit(
        "backtest_submit",
        resource=f"strategy:{strategy_id}",
        detail=f"提交策略回测（{backend} 后端，独立子进程执行）",
        backend=backend,
        start_date=start_date,
        end_date=end_date,
        universe=universe,
        asset_class=asset_class,
    )
    spawn_strategy_worker("backtest", strategy_id, {
        "start": start_date, "end": end_date, "backend": backend,
        "initial_capital": initial_capital, "trade_unit": trade_unit,
        "deal_price": deal_price, "slippage_bps": slippage_bps,
        "cost_buy": cost_buy, "cost_sell": cost_sell, "min_cost": min_cost,
        "universe": universe, "asset_class": asset_class,
    })
    return ApiResponse(ok=True, data={
        "message": f"策略 {strategy_id} 回测已提交（独立进程执行）",
        "strategy_id": strategy_id, "running": True,
    })


@router.get("/{strategy_id}/backtest-status")
async def get_backtest_status_api(strategy_id: int):
    """获取策略回测状态。

    回测在独立子进程（strategy_worker）执行，内存 backtest_status 只在提交时
    置 running；子进程结束后内存不会自动更新，这里用 pid 存活 + DB 推导对账：
    - worker 存活 → running
    - worker 已退出且内存仍 running → 查策略状态推导 completed/failed
    - web 重启内存丢失但 worker 存活 → 补 running
    """
    from app.services.strategy.strategy_worker import is_task_running

    status = backtest_status.get_status(strategy_id)
    if status.get("status") != "running":
        # web 重启后内存丢失：worker 还在跑则补为 running
        if is_task_running("backtest", strategy_id):
            backtest_status.set_running(strategy_id)
            status = backtest_status.get_status(strategy_id)
        return ApiResponse(ok=True, data={"strategy_id": strategy_id, **status})

    if is_task_running("backtest", strategy_id):
        return ApiResponse(ok=True, data={"strategy_id": strategy_id, **status})

    # worker 已退出，内存还是 running → 从 DB 推导结果
    strategy = await get_strategy(strategy_id)
    if strategy and strategy.get("status") == "backtest_failed":
        error = _extract_backtest_error(strategy.get("description"))
        backtest_status.set_failed(strategy_id, error or "回测失败")
        status = backtest_status.get_status(strategy_id)
    else:
        backtest_status.set_completed(strategy_id)
        status = backtest_status.get_status(strategy_id)
    return ApiResponse(ok=True, data={"strategy_id": strategy_id, **status})


def _extract_backtest_error(description: str | None) -> str | None:
    """从策略描述中提取 "[回测失败] xxx" 片段。"""
    if not description:
        return None
    marker = "[回测失败] "
    idx = description.rfind(marker)
    if idx == -1:
        return None
    return description[idx + len(marker):].strip() or None


@router.get("/{strategy_id}/backtest-results")
async def list_results_api(strategy_id: int, limit: int = Query(20, le=100)):
    items = await list_backtest_results(strategy_id=strategy_id, limit=limit)
    return ApiResponse(ok=True, data={"items": items, "total": len(items)})
