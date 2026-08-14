"""宏观指标 API：手动触发同步、查询指标序列、查询状态。"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, text

from app.core.database import get_db
from app.models.macro import MacroIndicator
from app.schemas.common import ApiResponse
from app.services.data.global_macro_sync import GLOBAL_MACRO_INDICATORS
from app.services.data.macro_sync import AKSHARE_INDICATORS, MACRO_INDICATORS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/macro", tags=["macro"])


@router.post("/sync")
async def macro_sync_api(
    broadcast: bool = Query(False, description="是否同时广播写 qlib bin（建议数据校验/补齐阶段执行；默认只拉数据入库）"),
):
    """手动触发宏观指标同步（东财 datacenter + akshare → PG，独立 worker 后台执行）。

    broadcast=False（默认）只拉数据入库 PG，不写 bin——回填期间点也安全；
    bin 广播放在数据校验/补齐阶段（日历对齐后）触发。

    长任务走独立 worker 子进程（spawn_sync_worker），避免占用 web 事件循环
    导致 uvicorn --reload 等待后台任务卡死。
    """
    from app.services.data.sync_progress import ensure_no_bin_sync
    if broadcast:
        ensure_no_bin_sync(suffix="；宏观 bin 广播需等当前同步完成（日历对齐）后执行")

    from app.services.data.sync_worker import spawn_sync_worker
    spawn_sync_worker("macro", "macro", broadcast=broadcast)
    return ApiResponse(ok=True, data={
        "message": "宏观指标同步已提交（独立进程后台执行）"
                   + ("" if not broadcast else "，含 bin 广播"),
        "indicators": sorted(MACRO_INDICATORS.keys()) + sorted(AKSHARE_INDICATORS.keys()),
        "broadcast": broadcast,
    })


@router.post("/sync-global")
async def macro_global_sync_api(
    broadcast: bool = Query(False, description="是否同时广播写 qlib bin（建议数据校验/补齐阶段执行；默认只拉数据入库）"),
):
    """手动触发全球宏观指标同步（FRED/CFTC/EIA → PG，独立 worker 后台执行）。

    broadcast=False（默认）只拉数据入库 PG，不写 bin——回填期间点也安全；
    bin 广播放在数据校验/补齐阶段（日历对齐后）触发。
    """
    from app.services.data.sync_progress import ensure_no_bin_sync
    if broadcast:
        ensure_no_bin_sync(suffix="；全球宏观 bin 广播需等当前同步完成（日历对齐）后执行")

    from app.services.data.sync_worker import spawn_sync_worker
    spawn_sync_worker("global_macro", "global_macro", broadcast=broadcast)
    return ApiResponse(ok=True, data={
        "message": "全球宏观指标同步已提交（独立进程后台执行）"
                   + ("" if not broadcast else "，含 bin 广播"),
        "indicators": sorted(GLOBAL_MACRO_INDICATORS.keys()),
        "broadcast": broadcast,
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


@router.get("/snapshot")
async def macro_snapshot_api(db=Depends(get_db)):
    """宏观快照：每个 (indicator, field_name) 返回最新一条 + 环比所需的上一条。

    供前端"最新值"卡片使用，替代全量拉历史数据只为取最新值的做法。
    """
    rows = await db.execute(text("""
        SELECT indicator, field_name,
               MAX(CASE WHEN rn = 1 THEN unit END) AS unit,
               MAX(CASE WHEN rn = 1 THEN available_date END) AS latest_date,
               MAX(CASE WHEN rn = 1 THEN value END) AS latest_value,
               MAX(CASE WHEN rn = 2 THEN available_date END) AS prev_date,
               MAX(CASE WHEN rn = 2 THEN value END) AS prev_value
        FROM (
            SELECT indicator, field_name, unit, available_date, value,
                   ROW_NUMBER() OVER (
                       PARTITION BY indicator, field_name
                       ORDER BY available_date DESC
                   ) AS rn
            FROM macro_indicator
        ) t
        WHERE rn <= 2
        GROUP BY indicator, field_name
        ORDER BY indicator, field_name
    """))
    items = [{
        "indicator": r.indicator,
        "field_name": r.field_name,
        "unit": r.unit,
        "latest_date": r.latest_date.isoformat() if r.latest_date else None,
        "latest_value": r.latest_value,
        "prev_date": r.prev_date.isoformat() if r.prev_date else None,
        "prev_value": r.prev_value,
    } for r in rows]
    return ApiResponse(ok=True, data={"items": items, "total": len(items)})
