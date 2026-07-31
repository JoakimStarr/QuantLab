"""量化数据管理 API：股票数据同步到 qlib bin、数据新鲜度、qlib 可用性。

数据源优先级由 config.quant.data_source 决定（默认 baostock）：
  - chenditc：下载 chenditc/investment_data 预构建 qlib_bin.tar.gz（全量历史, 每日更新）
  - baostock：一次拉取全市场某日K线（增量主源, 含ST标记+估值字段, 不限频）
  - akshare ：逐只爬取 AKShare 行情后转储 qlib bin（仅个股/指数兜底, 易被反爬）
"""
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, BackgroundTasks, Query
from sqlalchemy import select, func

from app.core.config import settings
from app.core.database import get_db
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
    # 读取数据时间范围（calendars/day.txt 首末行）
    earliest_date = None
    calendar_count = 0
    provider_uri = settings.qlib_provider_path
    if available:
        from pathlib import Path
        day_txt = Path(provider_uri) / "calendars" / "day.txt"
        if day_txt.exists():
            lines = [l.strip() for l in day_txt.read_text(encoding="utf-8").splitlines() if l.strip()]
            if lines:
                earliest_date = lines[0]
                calendar_count = len(lines)

    return ApiResponse(ok=True, data={
        "available": available,
        "message": message,
        "provider_uri": provider_uri,
        "earliest_date": earliest_date,
        "calendar_count": calendar_count,
    })


@router.get("/status")
async def data_status_api(db=Depends(get_db)):
    """股票量化数据新鲜度。"""
    # 检测超时同步：超过 30 分钟仍 syncing 的自动标记 failed
    await _detect_stale_sync(db)
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
        "last_updated": r.last_updated.strftime("%Y-%m-%dT%H:%M:%S+08:00") if r.last_updated else None,
        "status": r.status,
        "last_error": r.last_error,
        "qlib_dir": r.qlib_dir,
    } for r in rows]
    return ApiResponse(ok=True, data={"items": items, "total": total})


from app.services.data.smart_sync import run_smart_sync_task as _run_sync_task


async def _detect_stale_sync(db) -> int:
    """检测超时同步：上次更新超过 30 分钟仍为 syncing 的，自动标记为 failed。

    在状态查询时调用，避免容器重启后 syncing 状态长期残留。
    Returns:
        被标记为 failed 的记录数。
    """
    from datetime import timedelta
    threshold = datetime.now() - timedelta(minutes=30)
    result = await db.execute(
        select(StockDataStatus).where(
            StockDataStatus.status == "syncing",
            StockDataStatus.last_updated < threshold,
        )
    )
    stale_recs = result.scalars().all()
    for rec in stale_recs:
        rec.status = "failed"
        rec.last_error = (
            "[同步超时] 同步超过 30 分钟未完成，已自动标记失败\n"
            "建议: 同步可能卡死，建议重试。若反复超时，请检查网络稳定性或磁盘空间。"
        )
        rec.last_updated = datetime.now()
        logger.warning(
            "sync 超时: universe=%s last_updated 超过 30 分钟，标记 failed",
            rec.universe,
        )
    if stale_recs:
        await db.commit()
    return len(stale_recs)


@router.post("/sync")
async def sync_data_api(
    req: SyncDataRequest,
    background_tasks: BackgroundTasks,
    db=Depends(get_db),
):
    """触发股票数据同步到 qlib bin（后台执行）。

    数据源由 config.quant.data_source 决定（默认 baostock，可选 chenditc/akshare）。
    """
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError(
            "QLIB_NOT_AVAILABLE",
            "qlib 未安装，无法同步数据。请在 Python 3.11 环境安装 pyqlib 后重试",
            503,
        )

    universe = req.universe or settings.quant.get("universe", "csi300")
    # 智能同步：路径由 latest_date 距今天数自动判断（chenditc全量/baostock增量/同步当日）
    data_source = "smart_sync"
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

    # 立即标记为 syncing 并更新 last_updated（用于超时检测）
    if rec is None:
        rec = StockDataStatus(universe=universe, status="syncing")
        db.add(rec)
    else:
        rec.status = "syncing"
        rec.last_error = None
    rec.last_updated = datetime.now()
    await db.commit()

    background_tasks.add_task(_run_sync_task, req)
    return ApiResponse(ok=True, data={
        "message": f"已触发 universe={universe} 数据同步（后台执行，数据源={data_source}）",
        "universe": universe,
        "data_source": data_source,
        "start_date": req.start_date or settings.quant.get("default_backtest_period", {}).get("start"),
        "end_date": req.end_date,
    })


@router.post("/fallback-sync", summary="手动触发兜底同步")
async def fallback_sync_api(
    days: int = Query(5, ge=1, le=60, description="回溯天数"),
    source: str = Query("baostock", description="兜底源: baostock/akshare"),
):
    """手动触发兜底同步（当定时同步失败时用）。

    - source='baostock': 用 baostock 一次拉全市场（推荐，快）
    - source='akshare': 用 akshare 逐只爬（慢，仅个股/指数）

    依赖契约（并行开发中）: eod_incremental.incremental_sync_eod(days, universe, source) -> dict
    """
    if source not in ("baostock", "akshare"):
        return ApiResponse(ok=False, error={
            "code": "INVALID_SOURCE",
            "message": f"不支持的兜底源: {source}，仅支持 baostock/akshare",
            "status": 400,
        })
    from app.services.data.eod_incremental import incremental_sync_eod
    from app.core.executor import run_cpu
    # baostock/akshare 均以全市场为口径拉取；在 CPU 进程池中执行同步函数
    result = await run_cpu(incremental_sync_eod, days=days, universe="all", source=source)
    if not result.get("ok"):
        return ApiResponse(ok=False, error={
            "code": "FALLBACK_SYNC_FAILED",
            "message": result.get("error", "兜底同步失败"),
            "status": 500,
        })
    return ApiResponse(ok=True, data=result)
