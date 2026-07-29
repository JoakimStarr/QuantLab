"""因子扩展 API：对比、衰减分析、导出、自动入库"""
import csv
import io
import json
import logging
from fastapi import APIRouter, Query, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.core.errors import AppError
from app.schemas.common import ApiResponse
from app.services.factor.factor_compare import compare_factors, get_factor_decay
from app.services.factor.library import get_factor, add_factor, list_factors

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
    format: str = Query("csv", regex="^(csv|json)$"),
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
