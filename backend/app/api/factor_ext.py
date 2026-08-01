"""因子扩展 API：对比、衰减分析、导出、自动入库"""
import csv
import io
import json
import asyncio
import logging
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.core.errors import AppError
from app.schemas.common import ApiResponse
from app.services.factor.factor_compare import compare_factors, get_factor_decay
from app.services.factor.library import get_factor, list_factors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/factors", tags=["factor-ext"])


@router.post("/compare")
async def compare_factors_api(
    factor_ids: list[int] = Query(..., description="对比的因子 ID 列表"),
    start_date: str = Query(None),
    end_date: str = Query(None),
):
    """因子对比（添加5: 因子对比）"""
    from app.core.config import settings
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)
    if len(factor_ids) < 2:
        raise AppError("VALIDATION_ERROR", "至少选择 2 个因子进行对比", 422)
    period = settings.quant.get("default_backtest_period", {})
    start = start_date or period.get("start", "2020-01-01")
    end = end_date or period.get("end", "2024-12-31")
    result = await compare_factors(factor_ids, start, end)
    return ApiResponse(ok=True, data=result)


@router.get("/decay-check")
async def decay_check_api():
    """手动触发因子衰减检测（添加13: 因子衰减监控）"""
    from app.core.database import async_session
    from app.services.quant.factor_monitor import detect_all_factors_decay

    async with async_session() as session:
        result = await detect_all_factors_decay(db_session=session)
    return ApiResponse(ok=True, data=result)


@router.get("/{factor_id}/decay")
async def factor_decay_api(factor_id: int, max_lag: int = Query(20, le=40)):
    """因子 IC 衰减分析（添加6: 因子衰减分析）"""
    result = await get_factor_decay(factor_id, max_lag)
    if "error" in result:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": result["error"], "status": 404})
    return ApiResponse(ok=True, data=result)


@router.get("/export")
async def export_factors_api(
    category: str = Query(None),
    status: str = Query("active"),
    format: str = Query("csv", pattern="^(csv|json)$"),
):
    """因子导出（添加7: 因子导出）"""
    items, total = await list_factors(category=category, status=status, limit=500)
    if format == "json":
        import json
        content = json.dumps(items, ensure_ascii=False, indent=2, default=str)
        return StreamingResponse(
            io.BytesIO(content.encode("utf-8")),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=factors.json"},
        )
    # CSV
    output = io.StringIO()
    output.write("\ufeff")  # BOM for Excel
    writer = csv.writer(output)
    writer.writerow(["ID", "名称", "类别", "表达式", "IC", "RankIC", "ICIR", "换手", "状态", "创建时间"])
    for f in items:
        writer.writerow([
            f.get("id"), f.get("name"), f.get("category"),
            f.get("expression"), f.get("ic"), f.get("rank_ic"),
            f.get("icir"), f.get("turnover"), f.get("status"),
            f.get("created_at", ""),
        ])
    content = output.getvalue()
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=factors.csv"},
    )


@router.post("/auto-import")
async def auto_import_factors_api(
    task_id: int = Query(..., description="挖掘任务 ID"),
    ic_threshold: float = Query(0.03, description="IC 达标阈值"),
):
    """因子自动入库：从挖掘任务中导入 IC 达标的因子（添加12: 因子自动入库）"""
    from app.core.database import async_session
    from app.models.mining_task import MiningTask

    async with async_session() as session:
        task = await session.get(MiningTask, task_id)
        if task is None:
            return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "任务不存在", "status": 404})
        if task.status != "done":
            return ApiResponse(ok=False, error={"code": "TASK_NOT_DONE", "message": "任务尚未完成", "status": 400})
        result_ids = json.loads(task.result_factor_ids) if task.result_factor_ids else []

    if not result_ids:
        return ApiResponse(ok=False, error={"code": "NO_FACTORS", "message": "任务无结果因子", "status": 400})

    # 检查哪些因子已入库，哪些需要导入
    imported = []
    skipped = []
    for fid in result_ids:
        existing = await get_factor(fid)
        if existing:
            # 已入库，检查 IC 是否达标
            if existing.get("ic") and abs(existing["ic"]) >= ic_threshold:
                existing["status"] = "verified"
                imported.append({"id": fid, "name": existing["name"], "ic": existing["ic"], "action": "verified"})
            else:
                skipped.append({"id": fid, "ic": existing.get("ic"), "reason": "IC 未达标"})
        else:
            skipped.append({"id": fid, "reason": "因子不存在"})

    return ApiResponse(ok=True, data={
        "task_id": task_id,
        "imported": imported,
        "skipped": skipped,
        "total_imported": len(imported),
    })


@router.post("/seed-alpha158")
async def seed_alpha158_api():
    """导入 Alpha158 基准因子集（158 个 qlib 标准因子）。

    通过 WebSocket 推送 `alpha158_progress` 事件，包含 done/total/message 字段，
    前端可订阅 ws://<host>/ws 接收实时进度。
    """
    from app.services.factor.alpha158 import seed_alpha158
    from app.core.websocket_manager import ws_manager

    async def progress_cb(done: int, total: int, msg: str):
        await ws_manager.broadcast("alpha158_progress", {
            "done": done, "total": total, "message": msg,
        })

    result = await seed_alpha158(progress_callback=progress_cb)
    # 广播完成事件，方便前端区分 done/progress
    try:
        await ws_manager.broadcast("alpha158_progress", {
            "done": result.get("evaluated", 0),
            "total": result.get("count") or result.get("total") or 158,
            "message": result.get("message", "完成"),
            "finished": True,
        })
    except Exception:
        pass

    if not result.get("ok"):
        return ApiResponse(ok=False, error={"code": "ALPHA158_SEEDED", "message": result.get("error", "已导入"), "status": 400})
    return ApiResponse(ok=True, data=result)


@router.post("/backfill-alpha158-metrics")
async def backfill_alpha158_metrics_api():
    """为已导入但缺指标的 Alpha158 因子补算评价（IC/RankIC/ICIR/turnover）。

    用于修复历史遗留：导入时未触发评价导致指标为 NULL 的因子。
    线程池并发 + 预加载共用 label/close，158 个因子通常显著快于原版（取决于 IO）。
    进度通过 WebSocket `alpha158_progress` 事件推送。
    """
    from app.services.factor.alpha158 import backfill_alpha158_metrics
    from app.core.websocket_manager import ws_manager

    async def progress_cb(done: int, total: int, msg: str):
        await ws_manager.broadcast("alpha158_progress", {
            "done": done, "total": total, "message": msg,
        })

    result = await backfill_alpha158_metrics(progress_callback=progress_cb)
    try:
        await ws_manager.broadcast("alpha158_progress", {
            "done": result.get("evaluated", 0),
            "total": result.get("total", 0),
            "message": result.get("message", "补算完成"),
            "finished": True,
        })
    except Exception:
        pass

    return ApiResponse(ok=result.get("ok", True), data=result)


@router.get("/{factor_id}/quantile-analysis")
async def quantile_analysis_api(
    factor_id: int,
    n_groups: int = Query(5, ge=2, le=10),
    start_date: str = Query(None),
    end_date: str = Query(None),
):
    """因子分组收益评价（分层回测）：按因子值分 n_groups 组，返回各组净值、多空收益与单调性。"""
    from app.core.config import settings
    from app.services.quant.qlib_init import is_qlib_available
    from app.services.factor.library import get_factor
    from app.services.quant.factor_eval import (
        load_factor_values, load_label, compute_quantile_returns,
    )

    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)
    factor = await get_factor(factor_id)
    if not factor:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "因子不存在", "status": 404})

    period = settings.quant.get("default_backtest_period", {})
    start = start_date or period.get("start", "2020-01-01")
    end = end_date or period.get("end", "2024-12-31")

    def _compute_quantile():
        factor_df = load_factor_values(factor["expression"], start, end)
        return_df = load_label(start, end)
        return compute_quantile_returns(factor_df, return_df, n_groups=n_groups)

    try:
        result = await asyncio.get_running_loop().run_in_executor(None, _compute_quantile)
    except Exception as e:
        logger.warning("分组收益数据加载失败 factor_id=%s: %s", factor_id, e)
        return ApiResponse(ok=False, error={"code": "DATA_LOAD_ERROR", "message": str(e), "status": 500})

    if "error" in result:
        return ApiResponse(ok=False, error={"code": "NO_DATA", "message": result["error"], "status": 400})
    return ApiResponse(ok=True, data=result)


@router.post("/{factor_id}/neutralize")
async def neutralize_factor_api(
    factor_id: int,
    method: str = Query("market_cap", pattern="^(market_cap|industry|both)$"),
    start_date: str = Query(None),
    end_date: str = Query(None),
):
    """因子中性化：对比中性化前后 IC 指标

    method: market_cap(市值中性化) / industry(行业+市值中性化) / both(同 industry)
    """
    from app.core.config import settings
    from app.services.quant.qlib_init import is_qlib_available
    from app.services.factor.library import get_factor
    from app.services.quant.factor_eval import (
        load_factor_values, load_label, compute_ic,
    )

    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)
    factor = await get_factor(factor_id)
    if not factor:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "因子不存在", "status": 404})

    period = settings.quant.get("default_backtest_period", {})
    start = start_date or period.get("start", "2020-01-01")
    end = end_date or period.get("end", "2024-12-31")

    # 统一映射：both 等价于 industry（行业+市值）
    neutralize_method = "industry" if method in ("industry", "both") else "market_cap"

    def _compute_neutralize():
        factor_df_before = load_factor_values(factor["expression"], start, end)
        label_df = load_label(start, end)
        ic_before = compute_ic(factor_df_before, label_df)
        factor_df_after = load_factor_values(
            factor["expression"], start, end, neutralize=neutralize_method
        )
        ic_after = compute_ic(factor_df_after, label_df)
        return ic_before, ic_after

    try:
        ic_before, ic_after = await asyncio.get_running_loop().run_in_executor(None, _compute_neutralize)
    except Exception as e:
        logger.warning("因子中性化失败 factor_id=%s: %s", factor_id, e)
        return ApiResponse(ok=False, error={"code": "NEUTRALIZE_ERROR", "message": str(e), "status": 500})

    return ApiResponse(ok=True, data={
        "factor_id": factor_id,
        "factor_name": factor.get("name"),
        "method": method,
        "ic_before": ic_before,
        "ic_after": ic_after,
        "eval_start": start,
        "eval_end": end,
    })


# ==================== 因子深度分析 ====================
# 深度分析结果缓存：key=factor_id|start|end|horizon|n_groups|ic_window，TTL 1 小时
_deep_analysis_cache: dict = {}
_DEEP_CACHE_TTL = 3600


@router.get("/{factor_id}/deep-analysis")
async def deep_analysis_api(
    factor_id: int,
    start_date: str = Query(None),
    end_date: str = Query(None),
    horizon: int = Query(5, ge=1, le=60),
    n_groups: int = Query(5, ge=2, le=10),
    ic_window: int = Query(60, ge=20, le=250),
):
    """因子深度分析：IC 分布/时序/显著性 + horizon 调仓分层净值 + 换手率曲线 + 衰减。"""
    import time
    from app.core.config import settings
    from app.services.quant.qlib_init import is_qlib_available
    from app.core.executor import run_cpu
    from app.services.quant.factor_eval import deep_analyze_factor

    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)
    factor = await get_factor(factor_id)
    if not factor:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "因子不存在", "status": 404})

    period = settings.quant.get("default_backtest_period", {})
    start = start_date or period.get("start", "2020-01-01")
    end = end_date or period.get("end", "2024-12-31")
    universe = settings.quant.get("universe", "csi300")

    # 缓存命中直接返回（含 factor_id/factor_name）
    cache_key = f"{factor_id}|{start}|{end}|{horizon}|{n_groups}|{ic_window}"
    now = time.time()
    cached = _deep_analysis_cache.get(cache_key)
    if cached and (now - cached["ts"]) < _DEEP_CACHE_TTL:
        return ApiResponse(ok=True, data=cached["data"])

    # CPU 密集任务走进程池：deep_analyze_factor 为模块级函数，参数均可 pickle
    try:
        result = await run_cpu(
            deep_analyze_factor,
            factor["expression"], start, end, universe, horizon, n_groups, ic_window,
        )
    except ValueError as e:
        logger.warning("因子深度分析数据不足 factor_id=%s: %s", factor_id, e)
        return ApiResponse(ok=False, error={"code": "INSUFFICIENT_DATA", "message": str(e), "status": 400})
    except Exception as e:
        logger.warning("因子深度分析失败 factor_id=%s: %s", factor_id, e)
        return ApiResponse(ok=False, error={"code": "FACTOR_NOT_COMPUTABLE", "message": str(e), "status": 400})

    result["factor_id"] = factor_id
    result["factor_name"] = factor.get("name")
    _deep_analysis_cache[cache_key] = {"ts": now, "data": result}
    return ApiResponse(ok=True, data=result)
