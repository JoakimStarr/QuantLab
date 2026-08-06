"""量化数据管理 API：股票数据同步到 qlib bin、数据新鲜度、qlib 可用性。

数据源固定 baostock（全量回填 + 增量补缺）：
  - 全量回填：POST /quant/data/sync?years=N，从最新交易日向旧逐日拉全市场，
    写 qlib bin + PG stock_daily 全字段（手动触发，无自动同步）。
"""
import logging
from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy import select, func

from app.core.config import settings
from app.core.database import get_db
from app.models.stock_data_status import StockDataStatus
from app.schemas.common import ApiResponse

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
    disk_usage = None
    if available:
        from pathlib import Path
        import shutil
        from starlette.concurrency import run_in_threadpool
        day_txt = Path(provider_uri) / "calendars" / "day.txt"
        if day_txt.exists():
            lines = [line.strip() for line in day_txt.read_text(encoding="utf-8").splitlines() if line.strip()]
            if lines:
                earliest_date = lines[0]
                calendar_count = len(lines)

        # 磁盘占用：qlib 数据目录大小 + 所在文件系统剩余空间（rglob 较慢，放线程池避免阻塞事件循环）
        try:
            def _dir_bytes(p: Path) -> int:
                return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            dir_size = await run_in_threadpool(_dir_bytes, Path(provider_uri))
            du = await run_in_threadpool(shutil.disk_usage, str(Path(provider_uri)))
            disk_usage = {
                "dir_size_bytes": dir_size,
                "free_bytes": du.free,
                "total_bytes": du.total,
            }
        except Exception:
            disk_usage = None

    return ApiResponse(ok=True, data={
        "available": available,
        "message": message,
        "provider_uri": provider_uri,
        "earliest_date": earliest_date,
        "calendar_count": calendar_count,
        "disk_usage": disk_usage,
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


@router.get("/external-market")
async def external_market_status_api():
    """外盘隔夜因子最新状态（读最近一次同步缓存，不实时拉数据）。"""
    from app.services.data.external_market import get_external_market_state
    return ApiResponse(ok=True, data=get_external_market_state())


@router.post("/sync-external-market")
async def sync_external_market_api():
    """拉取外盘指数（标普/纳指/道指/恒指）→ 对齐 A股日历 → 广播成 bin 因子字段。

    轻量操作（4 个指数接口 + 广播写盘），直接在当前进程经 run_io_cpu 执行。
    建议在每个交易日 A股开盘后、外盘已收盘时手动触发一次。
    """
    from app.services.data.sync_progress import busy_message, writes_bins_active
    if writes_bins_active():
        return ApiResponse(ok=False, error={
            "code": "SYNC_IN_PROGRESS",
            "message": busy_message(),
            "status": 409,
        })
    from app.services.data.external_market import sync_external_market
    try:
        result = await sync_external_market()
    except Exception as e:
        logger.exception("外盘数据同步失败")
        return ApiResponse(ok=False, error={
            "code": "SYNC_FAILED",
            "message": f"外盘数据同步失败: {e}",
            "status": 500,
        })
    failed = [k for k, v in result.get("items", {}).items() if not v.get("ok")]
    if failed:
        return ApiResponse(ok=False, error={
            "code": "PARTIAL_FAILURE",
            "message": f"部分指数拉取失败: {', '.join(failed)}",
            "status": 502,
            "detail": result,
        })
    return ApiResponse(ok=True, data=result)
