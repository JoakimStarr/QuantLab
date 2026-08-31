"""每日晨报 API：取单日/最新、手动生成（幂等）、历史列表。"""
import logging
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query

from app.schemas.common import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/daily-report", tags=["daily-report"])


def _parse_date(value: str | None, name: str) -> date | None:
    """解析 YYYY-MM-DD 参数，格式非法返回 400（而不是 500）。"""
    if value is None:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{name} 日期格式非法: {value!r}，需要 YYYY-MM-DD") from None


@router.get("")
async def daily_report_get_api(date: str = Query(None, description="报告日期 YYYY-MM-DD（默认最新一条）")):
    """取某一天（或最新）的晨报；未生成返回 data=None。"""
    from app.services.data.daily_report import DISCLAIMER, get_report

    r = await get_report(_parse_date(date, "date"))
    if r is None:
        return ApiResponse(ok=True, data=None)
    r["disclaimer"] = DISCLAIMER
    return ApiResponse(ok=True, data=r)


@router.post("/generate")
async def daily_report_generate_api(
    date: str = Query(None, description="报告日期 YYYY-MM-DD（默认最新政策解读日）"),
    force: bool = Query(False, description="强制重新生成（忽略缓存）"),
):
    """生成（或取缓存）某一天的晨报；同一天已在生成时返回 409。"""
    from app.services.data.daily_report import (
        DISCLAIMER,
        DailyReportBusyError,
        generate_daily_report,
    )

    try:
        r = await generate_daily_report(_parse_date(date, "date"), force=force)
    except DailyReportBusyError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    r["disclaimer"] = DISCLAIMER
    return ApiResponse(ok=True, data=r)


@router.get("/history")
async def daily_report_history_api(
    limit: int = Query(30, ge=1, le=100, description="每页条数"),
    offset: int = Query(0, ge=0, description="偏移量"),
):
    """历史列表（不含大字段，仅 report_date/status/llm_status/error）。"""
    from app.services.data.daily_report import list_reports

    items = await list_reports(limit, offset)
    return ApiResponse(ok=True, data={"items": items, "total": len(items)})
