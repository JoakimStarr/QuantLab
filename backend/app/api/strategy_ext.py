"""策略扩展 API：参数扫描、回测对比、交易明细导出"""
import csv
import io
import json
import logging
from fastapi import APIRouter, Query, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.core.errors import AppError
from app.core.database import async_session
from app.schemas.common import ApiResponse
from app.models.backtest_result import BacktestResult
from app.services.strategy.manager import get_backtest_result, list_backtest_results
from app.services.strategy import backtest_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/strategies", tags=["strategy-ext"])


@router.get("/backtest-statuses")
async def list_backtest_statuses_api():
    """获取所有策略的回测状态。"""
    return ApiResponse(ok=True, data={"items": backtest_status.get_all_status()})


@router.post("/{strategy_id}/param-sweep")
async def param_sweep_api(
    strategy_id: int,
    background_tasks: BackgroundTasks,
    topk_list: list[int] = Query([10, 20, 30, 50], description="topk 参数列表"),
    rebalance_list: list[str] = Query(["day", "week"], description="调仓频率列表"),
    start_date: str = Query(None),
    end_date: str = Query(None),
):
    """参数扫描（添加8: 参数扫描）"""
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)

    from app.core.config import settings
    period = settings.quant.get("default_backtest_period", {})
    start = start_date or period.get("start", "2020-01-01")
    end = end_date or period.get("end", "2024-12-31")

    # 参数扫描在后台执行，结果存入 app.state
    from app.core.config import settings as cfg
    if not hasattr(cfg, '_sweep_results'):
        cfg._sweep_results = {}
    cfg._sweep_results[strategy_id] = {"status": "running", "results": []}

    async def _sweep_task():
        try:
            from app.services.strategy.param_sweep import run_param_sweep
            results = await run_param_sweep(strategy_id, topk_list, rebalance_list, start, end)
            cfg._sweep_results[strategy_id] = {"status": "done", "results": results}
        except Exception as e:
            logger.exception("参数扫描失败")
            cfg._sweep_results[strategy_id] = {"status": "failed", "error": str(e)}

    background_tasks.add_task(_sweep_task)
    return ApiResponse(ok=True, data={
        "message": f"参数扫描已提交（{len(topk_list)} x {len(rebalance_list)} = {len(topk_list)*len(rebalance_list)} 组合）",
        "strategy_id": strategy_id,
        "topk_list": topk_list,
        "rebalance_list": rebalance_list,
    })


@router.get("/{strategy_id}/param-sweep-results")
async def param_sweep_results_api(strategy_id: int):
    """获取参数扫描结果"""
    from app.core.config import settings as cfg
    result = getattr(cfg, '_sweep_results', {}).get(strategy_id)
    if result is None:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "无参数扫描结果", "status": 404})
    return ApiResponse(ok=True, data=result)


@router.post("/compare-backtests")
async def compare_backtests_api(
    result_ids: list[int] = Query(..., description="对比的回测结果 ID 列表"),
):
    """回测对比（添加9: 回测对比）"""
    if len(result_ids) < 2:
        raise AppError("VALIDATION_ERROR", "至少选择 2 个回测结果进行对比", 422)

    results = []
    nav_curves = []
    for rid in result_ids:
        r = await get_backtest_result(rid)
        if r:
            results.append(r)
            if r.get("nav_curve"):
                nav_curves.append({"result_id": rid, "curve": r["nav_curve"]})

    if len(results) < 2:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "部分回测结果不存在", "status": 404})

    # 对比表格
    comparison = []
    metrics_keys = ["annual_return", "annual_volatility", "sharpe", "sortino",
                    "max_drawdown", "calmar", "turnover", "win_rate",
                    "benchmark_return", "excess_return"]
    for r in results:
        row = {"result_id": r["id"], "strategy_id": r["strategy_id"],
               "start_date": r["start_date"], "end_date": r["end_date"]}
        for k in metrics_keys:
            row[k] = r.get(k)
        comparison.append(row)

    return ApiResponse(ok=True, data={
        "comparison": comparison,
        "nav_curves": nav_curves,
        "metrics_keys": metrics_keys,
    })


@router.get("/backtest-results/{result_id}/trades")
async def export_trades_api(result_id: int):
    """交易明细导出（添加10: 交易明细导出）"""
    r = await get_backtest_result(result_id)
    if r is None:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "回测结果不存在", "status": 404})

    # 从 nav_curve 或 metrics 中提取交易记录
    metrics = r.get("metrics") or {}
    trades = metrics.get("trades") or []

    if not trades:
        # 如果没有交易明细，从净值曲线生成持仓变动记录
        nav_curve = r.get("nav_curve") or []
        trades = []
        for i in range(1, len(nav_curve)):
            prev = nav_curve[i-1]
            curr = nav_curve[i]
            daily_ret = (curr.get("nav", 1) / prev.get("nav", 1) - 1) if prev.get("nav") else 0
            trades.append({
                "date": curr.get("date", ""),
                "nav": round(curr.get("nav", 0), 4),
                "daily_return": round(daily_ret, 4),
                "benchmark_nav": round(curr.get("benchmark", 0), 4),
                "excess": round(daily_ret - ((curr.get("benchmark",1)/prev.get("benchmark",1)-1) if prev.get("benchmark") else 0), 4),
            })

    # 生成 CSV
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)
    if trades:
        writer.writerow(trades[0].keys())
        for t in trades:
            writer.writerow(t.values())

    content = output.getvalue()
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=backtest_{result_id}_trades.csv"},
    )
