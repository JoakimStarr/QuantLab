"""经典策略库 API：教学卡片列表 + 一键回测。

前缀 /classic-strategies，与 /strategy-library（规则模板）和 /strategies/{id}（持久化策略）区分。
回测是阻塞计算，经 run_in_executor 放入线程池执行，不阻塞事件循环。
"""
import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.core.errors import AppError
from app.schemas.common import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/classic-strategies", tags=["classic-strategies"])


class ClassicBacktestRequest(BaseModel):
    key: str
    params: dict = Field(default_factory=dict, description="策略参数（可覆盖默认 topk/n_drop/调仓频率/标的等）")
    start: str
    end: str
    benchmark: str = None
    initial_capital: float = Field(10_000_000, ge=0, description="初始资金（元），默认 1000 万")


@router.get("")
async def list_classic_strategies_api():
    """经典策略教学卡片列表（tagline / 为什么有效 / 什么时候失效 / 参考文献）。"""
    from app.services.strategy.classic_library import list_classic_strategies

    items = list_classic_strategies()
    return ApiResponse(ok=True, data={"items": items, "total": len(items)})


@router.post("/backtest")
async def run_classic_strategy_api(req: ClassicBacktestRequest):
    """运行经典策略回测（因子型 → 截面 topk 链路；规则型 → 技术信号模板链路）。"""
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装或数据未同步", 503)

    from app.services.strategy.classic_library import get_classic_strategy, run_classic_strategy

    spec = get_classic_strategy(req.key)
    if spec is None:
        raise AppError("CLASSIC_NOT_FOUND", f"未知经典策略: {req.key}", 404)
    for d in (req.start, req.end):
        try:
            datetime.strptime(d, "%Y-%m-%d")
        except ValueError as e:
            raise AppError("VALIDATION_ERROR", f"日期格式应为 YYYY-MM-DD: {d}", 422) from e
    if req.start > req.end:
        raise AppError("VALIDATION_ERROR", "开始日期不能晚于结束日期", 422)

    params = {**req.params}
    if req.benchmark:
        params["benchmark"] = req.benchmark
    if req.initial_capital is not None:
        params["initial_capital"] = req.initial_capital

    try:
        result = await asyncio.get_running_loop().run_in_executor(
            None, run_classic_strategy, req.key, params, req.start, req.end,
        )
    except ValueError as e:
        raise AppError("BACKTEST_FAILED", str(e), 422) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("经典策略回测异常 key=%s", req.key)
        raise AppError("BACKTEST_FAILED", f"回测失败: {e}", 500) from e

    result["start_date"] = req.start
    result["end_date"] = req.end
    result["params"] = {
        k: v for k, v in params.items()
        if k in ("topk", "n_drop", "rebalance_freq", "universe", "symbols", "rule_params")
        and v is not None
    }
    if spec["kind"] == "factor":
        result["expression"] = spec.get("expression")
        result["universe"] = params.get("universe", spec["defaults"].get("universe"))
        result["params"].setdefault("universe", params.get("universe", spec["defaults"].get("universe")))

    # 自动保存历史（配置 + 结果快照）；保存失败不阻断回测
    from app.services.classic_history import save_classic_history
    history_id = await save_classic_history(result)
    if history_id is not None:
        result["history_id"] = history_id

    return ApiResponse(ok=True, data=result)


@router.get("/history")
async def list_classic_history_api(
    limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0),
):
    """经典策略回测历史摘要列表（按时间倒序，不含净值/成交大字段）。"""
    from app.services.classic_history import list_classic_history

    items, total = await list_classic_history(limit=limit, offset=offset)
    return ApiResponse(ok=True, data={"items": items, "total": total})


@router.get("/history/all")
async def list_combined_history_api(
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
):
    """策略库统一历史列表：经典 + 规则模板回测历史合并（含 source 字段）。"""
    from app.services.classic_history import list_combined

    items, total = await list_combined(limit=limit, offset=offset)
    return ApiResponse(ok=True, data={"items": items, "total": total})


@router.get("/history/{history_id}")
async def get_classic_history_api(history_id: int):
    """经典策略回测历史完整详情（含参数/指标/净值曲线/成交明细）。"""
    from app.services.classic_history import get_classic_history

    item = await get_classic_history(history_id)
    if item is None:
        raise AppError("NOT_FOUND", "回测历史不存在", 404)
    return ApiResponse(ok=True, data=item)


@router.delete("/history/{history_id}")
async def delete_classic_history_api(history_id: int):
    """软删除经典策略回测历史记录。"""
    from app.services.classic_history import delete_classic_history

    ok = await delete_classic_history(history_id)
    if not ok:
        raise AppError("NOT_FOUND", "回测历史不存在", 404)
    return ApiResponse(ok=True, data={"history_id": history_id, "message": "回测历史已删除"})


class HistoryCompareRequest(BaseModel):
    """跨来源历史对比：items=[{source, id}]，source ∈ classic/rule。"""

    items: list[dict] = Field(min_length=2, description="要对比的历史项 [{source, id}]")


@router.post("/history/compare")
async def compare_history_api(req: HistoryCompareRequest):
    """策略库历史对比：读取经典/规则历史中的指标与净值曲线，返回对比结果。

    返回与 /strategies/compare-backtests 同构的 {comparison, nav_curves, metrics_keys}，
    前端可直接复用现有回测对比页的渲染逻辑。
    """
    from app.services.classic_history import get_history_nav

    comparison = []
    nav_curves = []
    metrics_keys = ["annual_return", "annual_volatility", "sharpe", "sortino",
                    "max_drawdown", "calmar", "win_rate", "benchmark_return", "excess_return"]
    for it in req.items:
        source = it.get("source", "classic")
        rid = it.get("id")
        item = await get_history_nav(rid, source)
        if not item:
            continue
        row = {
            "result_id": rid,
            "history_id": rid,
            "source": source,
            "strategy_id": None,
            "name": item.get("template_name") or item.get("name"),
            "start_date": item.get("start_date"),
            "end_date": item.get("end_date"),
        }
        for k in metrics_keys:
            row[k] = item.get(k)
        comparison.append(row)
        nav_curve = item.get("nav_curve")
        if nav_curve:
            nav_curves.append({"result_id": rid, "curve": nav_curve})

    if len(comparison) < 2:
        raise AppError("NOT_FOUND", "至少需要 2 条有效历史记录进行对比", 404)
    return ApiResponse(ok=True, data={
        "comparison": comparison,
        "nav_curves": nav_curves,
        "metrics_keys": metrics_keys,
    })


@router.get("/{key}/factor-analysis")
async def factor_analysis_api(
    key: str,
    start_date: str = Query(None),
    end_date: str = Query(None),
    universe: str = Query(None),
):
    """经典策略因子表现：对截面因子型策略的表达式做 IC / 分层收益分析。

    复用因子深度分析链路（load_factor_values + compute_ic + compute_quantile_returns），
    用于教学卡片上的「查看因子表现」入口。
    """
    from app.core.config import settings
    from app.services.quant.qlib_init import is_qlib_available
    from app.services.strategy.classic_library import get_classic_strategy

    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装或数据未同步", 503)
    spec = get_classic_strategy(key)
    if spec is None or spec["kind"] != "factor":
        raise AppError("CLASSIC_NOT_FOUND", f"经典策略不存在或非截面因子型: {key}", 404)

    period = settings.quant.get("default_backtest_period", {})
    start = start_date or period.get("start", "2020-01-01")
    end = end_date or period.get("end", "2024-12-31")
    universe = universe or spec["defaults"].get("universe") or settings.quant.get("universe", "csi300")

    from app.services.quant.factor_eval import (
        load_factor_values, load_label, compute_ic, compute_quantile_returns,
    )

    def _compute():
        factor_df = load_factor_values(spec["expression"], start, end, universe=universe)
        return_df = load_label(start, end, universe=universe)
        ic = compute_ic(factor_df, return_df)
        quantile = compute_quantile_returns(factor_df, return_df, n_groups=5)
        return ic, quantile

    try:
        ic, quantile = await asyncio.get_running_loop().run_in_executor(None, _compute)
    except Exception as e:  # noqa: BLE001
        logger.warning("经典策略因子分析失败 key=%s: %s", key, e)
        raise AppError("FACTOR_ANALYSIS_FAILED", f"因子分析失败: {e}", 500) from e

    if "error" in quantile:
        return ApiResponse(ok=False, error={"code": "NO_DATA", "message": quantile["error"], "status": 400})

    return ApiResponse(ok=True, data={
        "key": key,
        "name": spec["name"],
        "expression": spec["expression"],
        "universe": universe,
        "start_date": start,
        "end_date": end,
        "ic": ic,
        "quantile": quantile,
    })