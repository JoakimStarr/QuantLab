"""量化数据管理 API：股票数据同步到 qlib bin、数据新鲜度、qlib 可用性。

数据源固定 baostock（全量回填 + 增量补缺）：
  - 全量回填：POST /quant/data/sync?years=N，从最新交易日向旧逐日拉全市场，
    写 qlib bin + PG stock_daily 全字段（手动触发，无自动同步）。
"""
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, Query
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

    两类回收：
    - auto 触发的同步：超过 30 分钟未完成即标记失败（手动全量回填本来就可能远超 30 分钟）。
    - 手动/自动通用：独立 worker 子进程已死亡（worker_pid 无存活进程）但 DB 仍残留 syncing，
      说明同步进程挂了，立即标记失败，避免永久卡 syncing 挡住后续同步。

    在状态查询时调用，避免容器重启后 syncing 状态长期残留。
    Returns:
        被标记为 failed 的记录数。
    """
    from datetime import timedelta
    from app.services.data.sync_progress import get_progress
    threshold = datetime.now() - timedelta(minutes=30)
    result = await db.execute(
        select(StockDataStatus).where(
            StockDataStatus.status == "syncing",
            StockDataStatus.sync_trigger == "auto",
            StockDataStatus.last_updated < threshold,
        )
    )
    stale_recs = result.scalars().all()
    marked = set()

    # 手动触发的同步：若 worker 进程已死 → 也标记失败
    prog = get_progress()
    worker_dead = bool(prog and prog.get("worker_pid"))
    if worker_dead:
        from app.services.data.sync_progress import _pid_alive
        worker_dead = not _pid_alive(prog.get("worker_pid"))

    if worker_dead:
        # worker 崩溃前若已通过 finish_progress(False, err) 写入真实错误，
        # 直接透传，而不是用"[worker 退出]"通用提示（用户无需翻日志）。
        real_error = (prog or {}).get("error")
        result2 = await db.execute(
            select(StockDataStatus).where(
                StockDataStatus.status == "syncing",
                StockDataStatus.last_updated < datetime.now() - timedelta(minutes=1),
            )
        )
        for rec in result2.scalars().all():
            if rec.universe not in marked:
                rec.status = "failed"
                if real_error:
                    rec.last_error = (
                        f"[worker 退出] {real_error}\n"
                        "建议: 检查 logs/sync_worker_backfill.log 后重试同步。"
                    )
                else:
                    rec.last_error = (
                        "[worker 退出] 同步进程已退出（可能被杀/崩溃），已标记失败\n"
                        "建议: 检查 logs/sync_worker_backfill.log 后重试同步。"
                    )
                rec.last_updated = datetime.now()
                logger.warning("sync 超时: universe=%s 的 worker 进程已死，标记 failed", rec.universe)
                marked.add(rec.universe)

    for rec in stale_recs:
        if rec.universe in marked:
            continue
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
    if stale_recs or marked:
        await db.commit()
    return len(stale_recs) + len(marked)


@router.post("/sync")
async def sync_data_api(
    req: SyncDataRequest,
    db=Depends(get_db),
):
    """触发 baostock 全量回填同步（独立 worker 子进程执行，手动触发）。

    years 指定回填年数（从最新向旧）；不传默认 config.quant.backfill_years（默认5）。

    同步在独立子进程（app.services.data.sync_worker）中运行，与 web 进程解耦：
    uvicorn --reload 重启不会等它，也不会误杀它。状态写 DB、进度写共享文件，
    前端通过 /quant/data/status 与 /quant/data/sync-progress 实时查看。
    """
    universe = req.universe or settings.quant.get("universe", "csi300")
    years = req.years or int(settings.quant.get("backfill_years", 5))
    data_source = "baostock"
    # 若已有一个真实活跃的同步（内存或存活 worker），拒绝重复提交，避免并发下载：
    # baostock 禁止并发连接，两个回填并发会互相拖垮。
    from app.services.data.sync_progress import get_progress, sync_is_active
    if sync_is_active():
        active = get_progress()
        return ApiResponse(ok=False, error={
            "code": "SYNC_IN_PROGRESS",
            "message": f"正在同步中，请稍候（当前 universe={active.get('universe') if active else '?'}）",
            "status": 409,
        })
    existing = await db.execute(
        select(StockDataStatus).where(StockDataStatus.universe == universe)
    )
    rec = existing.scalar_one_or_none()
    if rec and rec.status == "syncing":
        # 无活跃 worker 但 DB 残留 syncing：允许重新触发，由 worker 状态覆盖
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

    # 启动独立 worker 子进程（脱离 web 进程组，后台运行）
    from app.services.data.sync_worker import spawn_sync_worker
    spawn_sync_worker("backfill", universe, years=years)
    return ApiResponse(ok=True, data={
        "message": f"已触发 universe={universe} 数据同步（baostock 回填 {years} 年，独立进程后台执行）",
        "universe": universe,
        "data_source": data_source,
        "years": years,
    })


@router.post("/sync-calendar")
async def sync_calendar_api():
    """以数据库 stock_daily 的交易日为准，重建 qlib 日历 day.txt。

    不下载任何数据，只把 day.txt 与已落库日期对齐（数据库是权威）。
    回填流程内部每批落库后也会自动更新日历；此端点用于手动修复
    day.txt 与数据库不一致的情况（如历史残留、中断后的孤儿写入）。
    """
    from app.services.data.baostock_backfill import rebuild_calendar_from_db
    dates = await rebuild_calendar_from_db()
    return ApiResponse(ok=True, data={
        "message": f"日历已与数据库同步，共 {len(dates)} 个交易日",
        "calendar_count": len(dates),
        "calendar_start": dates[0] if dates else None,
        "calendar_end": dates[-1] if dates else None,
    })


@router.post("/fallback-sync", summary="手动触发兜底同步")
async def fallback_sync_api(
    days: int = Query(5, ge=1, le=60, description="回溯天数"),
    source: str = Query("baostock", description="兜底源: baostock/akshare"),
):
    """手动触发兜底同步（独立 worker 后台执行）。

    - source='baostock': 用 baostock 一次拉全市场（推荐，快）
    - source='akshare': 用 akshare 逐只爬（慢，仅个股/指数）

    同步在独立 worker 子进程（app.services.data.sync_worker --kind eod）中运行，
    与 web 进程解耦，uvicorn --reload 重启不会等它。结果写 data/eod_last_result.json，
    前端通过 /quant/data/eod-result 轮询。
    """
    if source not in ("baostock", "akshare"):
        return ApiResponse(ok=False, error={
            "code": "INVALID_SOURCE",
            "message": f"不支持的兜底源: {source}，仅支持 baostock/akshare",
            "status": 400,
        })
    from app.services.data.sync_progress import sync_is_active
    if sync_is_active():
        return ApiResponse(ok=False, error={
            "code": "SYNC_IN_PROGRESS",
            "message": "正在同步/修复中，请稍候（存在活跃同步任务）",
            "status": 409,
        })
    from app.services.data.sync_worker import spawn_sync_worker
    spawn_sync_worker("eod", "all", days=days, source=source)
    return ApiResponse(ok=True, data={
        "message": f"兜底同步已提交（source={source}, days={days}），独立进程后台执行中",
    })
