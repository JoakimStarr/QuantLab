"""量化数据管理 API：股票数据同步到 qlib bin、数据新鲜度、qlib 可用性。"""
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy import select, func

from app.core.config import settings
from app.core.database import get_db, async_session
from app.core.errors import AppError
from app.models.stock_data_status import StockDataStatus
from app.schemas.common import ApiResponse
from app.schemas.quant import SyncDataRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quant/data", tags=["quant-data"])


@router.get("/qlib-status")
async def qlib_status_api():
    """检测 qlib 是否可用（不抛异常）。"""
    from app.services.quant.qlib_init import is_qlib_available, QlibNotAvailableError
    try:
        available = await is_qlib_available()
        message = "qlib 已就绪" if available else "qlib 未安装或初始化失败"
    except QlibNotAvailableError as e:
        available = False
        message = str(e)
    return ApiResponse(ok=True, data={
        "available": available,
        "message": message,
        "provider_uri": settings.qlib_provider_path,
    })


@router.get("/status")
async def data_status_api(db=Depends(get_db)):
    """股票量化数据新鲜度。"""
    count_result = await db.execute(select(func.count()).select_from(StockDataStatus))
    total = count_result.scalar() or 0
    result = await db.execute(
        select(StockDataStatus).order_by(StockDataStatus.last_updated.desc())
    )
    rows = result.scalars().all()
    items = [{
        "universe": r.universe,
        "latest_date": r.latest_date,
        "row_count": r.row_count,
        "stock_count": r.stock_count,
        "last_updated": r.last_updated.isoformat() if r.last_updated else None,
        "status": r.status,
        "last_error": r.last_error,
        "qlib_dir": r.qlib_dir,
    } for r in rows]
    return ApiResponse(ok=True, data={"items": items, "total": total})


async def _run_sync_task(req: SyncDataRequest):
    """后台执行数据同步（独立 session）。"""
    universe = req.universe or settings.quant.get("universe", "csi300")
    period = settings.quant.get("default_backtest_period", {})
    start_date = req.start_date or period.get("start", "2020-01-01")
    end_date = req.end_date or datetime.now().strftime("%Y-%m-%d")

    # 标记 syncing
    async with async_session() as session:
        existing = await session.execute(
            select(StockDataStatus).where(StockDataStatus.universe == universe)
        )
        rec = existing.scalar_one_or_none()
        if rec is None:
            rec = StockDataStatus(universe=universe, status="syncing")
            session.add(rec)
        else:
            rec.status = "syncing"
            rec.last_error = None
        await session.commit()

    try:
        from app.services.quant.data_adapter import sync_to_qlib, get_universe
        from app.services.quant.qlib_init import QlibNotAvailableError
        codes = req.codes
        if codes is None:
            codes = await get_universe()
        summary = await sync_to_qlib(start_date, end_date, codes=codes)

        async with async_session() as session:
            existing = await session.execute(
                select(StockDataStatus).where(StockDataStatus.universe == universe)
            )
            rec = existing.scalar_one_or_none()
            if rec is None:
                rec = StockDataStatus(universe=universe)
                session.add(rec)
            rec.latest_date = end_date
            rec.stock_count = summary.get("done", 0)
            rec.row_count = summary.get("done", 0)
            rec.status = "ok"
            rec.last_error = None
            rec.qlib_dir = summary.get("qlib_dir")
            rec.last_updated = datetime.now()
            await session.commit()
    except QlibNotAvailableError as e:
        await _mark_failed(universe, str(e))
        logger.error("qlib 不可用: %s", e)
    except Exception as e:
        await _mark_failed(universe, str(e))
        logger.exception("数据同步失败")


async def _mark_failed(universe: str, error: str):
    logger.error("数据同步失败 universe=%s: %s", universe, error[:500])
    async with async_session() as session:
        existing = await session.execute(
            select(StockDataStatus).where(StockDataStatus.universe == universe)
        )
        rec = existing.scalar_one_or_none()
        if rec is None:
            rec = StockDataStatus(universe=universe)
            session.add(rec)
        rec.status = "failed"
        rec.last_error = error[:500]
        rec.last_updated = datetime.now()
        await session.commit()


@router.post("/sync")
async def sync_data_api(
    req: SyncDataRequest,
    background_tasks: BackgroundTasks,
    db=Depends(get_db),
):
    """触发股票数据同步到 qlib bin（后台执行）。"""
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError(
            "QLIB_NOT_AVAILABLE",
            "qlib 未安装，无法同步数据。请在 Python 3.11 环境安装 pyqlib 后重试",
            503,
        )

    universe = req.universe or settings.quant.get("universe", "csi300")
    # 若正在同步则拒绝（带超时检测：超过10分钟视为卡死，允许重新同步）
    existing = await db.execute(
        select(StockDataStatus).where(StockDataStatus.universe == universe)
    )
    rec = existing.scalar_one_or_none()
    if rec and rec.status == "syncing":
        from datetime import timedelta
        if rec.last_updated and datetime.now() - rec.last_updated < timedelta(minutes=10):
            return ApiResponse(ok=False, error={
                "code": "SYNC_IN_PROGRESS",
                "message": f"universe={universe} 正在同步中，请稍后",
                "status": 409,
            })
        # 超时，允许覆盖
        logger.warning("universe=%s 上次同步超时（%s），允许重新同步", universe, rec.last_updated)

    # 立即标记为 syncing 避免 TOCTOU 竞态（不更新 last_updated，仅在实际完成时更新）
    if rec is None:
        rec = StockDataStatus(universe=universe, status="syncing")
        db.add(rec)
    else:
        rec.status = "syncing"
        rec.last_error = None
    await db.commit()

    background_tasks.add_task(_run_sync_task, req)
    return ApiResponse(ok=True, data={
        "message": f"已触发 universe={universe} 数据同步（后台执行）",
        "universe": universe,
        "start_date": req.start_date or settings.quant.get("default_backtest_period", {}).get("start"),
        "end_date": req.end_date,
    })
