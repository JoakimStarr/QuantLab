"""宏观指标 API：手动触发同步、查询指标序列、查询状态。"""
import logging
from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import select

from app.core.database import get_db
from app.schemas.common import ApiResponse
from app.services.data.macro_sync import (
    AKSHARE_INDICATORS,
    MACRO_INDICATORS,
    run_macro_sync_task,
)
from app.models.macro import MacroIndicator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/macro", tags=["macro"])


@router.post("/sync")
async def macro_sync_api(background_tasks: BackgroundTasks):
    """手动触发宏观指标同步（东财 datacenter + akshare → PG → qlib bin 广播）。"""
    background_tasks.add_task(run_macro_sync_task)
    return ApiResponse(ok=True, data={
        "message": "宏观指标同步已提交（后台执行）",
        "indicators": sorted(MACRO_INDICATORS.keys()) + sorted(AKSHARE_INDICATORS.keys()),
    })


@router.get("/indicators")
async def macro_indicators_api(
    indicator: str = Query(None, description="指标代码 PMI/CPI/PPI/GDP，空则返回全部"),
    field: str = Query(None, description="字段名 pmi/cpi/ppi/gdp 等，空则返回该指标全部字段"),
    start: str = Query(None, description="开始日期 YYYY-MM-DD（按 available_date）"),
    end: str = Query(None, description="结束日期 YYYY-MM-DD（按 available_date）"),
    db=Depends(get_db),
):
    """查询宏观指标序列（按 available_date 升序）。"""
    query = select(MacroIndicator).order_by(
        MacroIndicator.indicator, MacroIndicator.field_name, MacroIndicator.available_date
    )
    if indicator:
        query = query.where(MacroIndicator.indicator == indicator.upper())
    if field:
        query = query.where(MacroIndicator.field_name == field.lower())
    if start:
        query = query.where(MacroIndicator.available_date >= datetime.strptime(start, "%Y-%m-%d").date())
    if end:
        query = query.where(MacroIndicator.available_date <= datetime.strptime(end, "%Y-%m-%d").date())

    result = await db.execute(query)
    rows = result.scalars().all()
    items = [{
        "indicator": r.indicator,
        "field_name": r.field_name,
        "report_date": r.report_date.isoformat() if r.report_date else None,
        "available_date": r.available_date.isoformat() if r.available_date else None,
        "value": r.value,
        "unit": r.unit,
    } for r in rows]
    return ApiResponse(ok=True, data={"items": items, "total": len(items)})


@router.get("/status")
async def macro_status_api(db=Depends(get_db)):
    """宏观数据状态：各指标最新日期与记录数。"""
    from sqlalchemy import func

    result = await db.execute(
        select(
            MacroIndicator.indicator,
            MacroIndicator.field_name,
            func.count().label("cnt"),
            func.max(MacroIndicator.available_date).label("latest"),
        ).group_by(MacroIndicator.indicator, MacroIndicator.field_name)
    )
    items = [{
        "indicator": r.indicator,
        "field_name": r.field_name,
        "count": r.cnt,
        "latest_date": r.latest.isoformat() if r.latest else None,
    } for r in result]
    return ApiResponse(ok=True, data={"items": items, "total": len(items)})
