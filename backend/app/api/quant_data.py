"""量化数据管理 API：股票数据同步到 qlib bin、数据新鲜度、qlib 可用性。

数据源固定 baostock（全量回填 + 增量补缺）：
  - 全量回填：POST /quant/data/sync?years=N，从最新交易日向旧逐日拉全市场，
    写 qlib bin + PG stock_daily 全字段（手动触发，无自动同步）。
"""
from app.services.data.baostock_backfill import run_baostock_backfill_task as _run_sync_task
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, BackgroundTasks, Query
from sqlalchemy import select, func

from app.core.config import settings
from app.core.database import get_db
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
            lines = [line.strip() for line in day_txt.read_text(encoding="utf-8").splitlines() if line.strip()]
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


async def _detect_stale_sync(db) -> int:
    """检测超时同步：上次更新超过 30 分钟仍为 syncing 的，自动标记为 failed。

    仅对自动触发的同步（sync_trigger='auto'）生效——手动"开始同步"下载全市场
    数据本来就慢，可能远超 30 分钟，不能被误杀；手动同步由任务自身的
    120s 单日拉取超时 + 启动时 recover_stale_sync 兜底，不会永久卡死。

    在状态查询时调用，避免容器重启后 syncing 状态长期残留。
    Returns:
        被标记为 failed 的记录数。
    """
    from datetime import timedelta
    threshold = datetime.now() - timedelta(minutes=30)
    result = await db.execute(
        select(StockDataStatus).where(
            StockDataStatus.status == "syncing",
            StockDataStatus.sync_trigger == "auto",
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
    """触发 baostock 全量回填同步（后台执行，手动触发）。

    years 指定回填年数（从最新向旧）；不传默认 config.quant.backfill_years（默认5）。
    """
    universe = req.universe or settings.quant.get("universe", "csi300")
    years = req.years or int(settings.quant.get("backfill_years", 5))
    data_source = "baostock"
    # 若已有同步在真实执行（内存进度活跃），拒绝重复提交，避免并发重复下载：
    # baostock 禁止并发连接，两个回填并发会互相拖垮。
    from app.services.data.sync_progress import get_progress
    active = get_progress()
    if active and active.get("status") not in ("done", "failed", "idle", None):
        return ApiResponse(ok=False, error={
            "code": "SYNC_IN_PROGRESS",
            "message": f"正在同步中，请稍候（当前 universe={active.get('universe')}）",
            "status": 409,
        })
    existing = await db.execute(
        select(StockDataStatus).where(StockDataStatus.universe == universe)
    )
    rec = existing.scalar_one_or_none()
    if rec and rec.status == "syncing":
        # 进程内无活跃同步但 DB 仍残留 syncing（如容器重启/进程被杀），
        # 允许重新触发，由启动时 recover_stale_sync 统一收尾
        logger.warning("universe=%s 残留 syncing 状态（%s），允许重新同步", universe, rec.last_updated)

    # 立即标记为 syncing 并更新 last_updated（手动触发，不限时）
    if rec is None:
        rec = StockDataStatus(universe=universe, status="syncing")
        db.add(rec)
    else:
        rec.status = "syncing"
        rec.last_error = None
    rec.sync_trigger = "manual"
    rec.last_updated = datetime.now()
    await db.commit()

    background_tasks.add_task(_run_sync_task, req)
    return ApiResponse(ok=True, data={
        "message": f"已触发 universe={universe} 数据同步（baostock 回填 {years} 年，后台执行）",
        "universe": universe,
        "data_source": data_source,
        "years": years,
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
