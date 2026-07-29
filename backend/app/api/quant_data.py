"""量化数据管理 API：股票数据同步到 qlib bin、数据新鲜度、qlib 可用性。

数据源优先级由 config.quant.data_source 决定（默认 chenditc）：
  - chenditc：下载 chenditc/investment_data 预构建 qlib_bin.tar.gz（推荐，每日更新）
  - akshare ：逐只爬取 AKShare 行情后转储 qlib bin（兜底，易被反爬）
"""
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


def _collect_qlib_stats(qlib_dir: str) -> dict:
    """从解压后的 qlib bin 目录统计 latest_date / stock_count / row_count。

    - latest_date: calendars/day.txt 末行（最近交易日）
    - stock_count: instruments/all.txt 非空行数（股票数量）
    - row_count: instruments 目录下所有 .txt 非空行数之和（成分记录总数）
    """
    from pathlib import Path
    base = Path(qlib_dir)
    # 1. 最近交易日：calendars/day.txt 末行
    latest_date = None
    day_txt = base / "calendars" / "day.txt"
    if day_txt.exists():
        lines = [l.strip() for l in day_txt.read_text(encoding="utf-8").splitlines() if l.strip()]
        if lines:
            latest_date = lines[-1]
    # 2. 统计 instruments 目录
    inst_dir = base / "instruments"
    stock_count = 0
    row_count = 0
    if inst_dir.exists():
        for txt in sorted(inst_dir.glob("*.txt")):
            n = sum(1 for l in txt.read_text(encoding="utf-8").splitlines() if l.strip())
            row_count += n
            if txt.name == "all.txt":
                stock_count = n
    return {
        "latest_date": latest_date,
        "stock_count": stock_count,
        "row_count": row_count,
    }


async def _sync_via_chenditc(universe: str) -> dict:
    """通过 chenditc 预构建 tarball 同步 qlib bin 数据。

    下载 chenditc/investment_data 最新 release 的 qlib_bin.tar.gz，
    解压到 qlib provider 目录，并从解压结果统计新鲜度指标。
    """
    import asyncio
    from app.services.data.chenditc_client import download_qlib_bin, get_latest_release_info
    from app.services.data.sync_progress import init_progress, finish_progress, clear_progress
    from app.models.sync_history import SyncHistory

    qlib_dir = settings.qlib_provider_path

    # 初始化进度跟踪
    release = get_latest_release_info()
    total_mb = release.get("size", 0) / 1024 / 1024 if "error" not in release else 0
    init_progress(universe, "chenditc", total_mb=total_mb)

    # 记录同步历史
    history = SyncHistory(
        universe=universe, data_source="chenditc", status="running",
        started_at=datetime.now(),
        version=release.get("version") if "error" not in release else None,
        release_date=release.get("date") if "error" not in release else None,
        file_size_mb=round(total_mb, 1) if total_mb else None,
    )
    async with async_session() as session:
        session.add(history)
        await session.commit()
        await session.refresh(history)
    history_id = history.id

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, download_qlib_bin, qlib_dir)
        finish_progress(True)

        # 更新同步历史
        stats = _collect_qlib_stats(qlib_dir)
        async with async_session() as session:
            h = await session.get(SyncHistory, history_id)
            if h:
                h.status = "ok"
                h.finished_at = datetime.now()
                h.duration_seconds = (datetime.now() - history.started_at).total_seconds()
                h.latest_date = stats["latest_date"]
                h.stock_count = stats["stock_count"]
                h.row_count = stats["row_count"]
                await session.commit()

        # 广播 WebSocket 通知
        from app.core.websocket_manager import ws_manager
        await ws_manager.broadcast("sync_complete", {
            "universe": universe,
            "latest_date": stats["latest_date"],
            "stock_count": stats["stock_count"],
        })
    except Exception as e:
        finish_progress(False, str(e))
        # 更新同步历史为失败
        async with async_session() as session:
            h = await session.get(SyncHistory, history_id)
            if h:
                h.status = "failed"
                h.finished_at = datetime.now()
                h.error = str(e)[:500]
                await session.commit()
        clear_progress()
        raise

    stats = _collect_qlib_stats(qlib_dir)
    logger.info(
        "chenditc 同步完成 universe=%s version=%s latest_date=%s stocks=%d rows=%d",
        universe, result.get("version"), stats["latest_date"],
        stats["stock_count"], stats["row_count"],
    )
    return {
        "universe": universe,
        "latest_date": stats["latest_date"],
        "stock_count": stats["stock_count"],
        "row_count": stats["row_count"],
        "qlib_dir": qlib_dir,
        "version": result.get("version"),
        "release_date": result.get("date"),
        "data_source": "chenditc",
    }


async def _sync_via_akshare(universe: str, req: SyncDataRequest) -> dict:
    """通过 akshare 增量同步EOD数据（国内源，访问快）"""
    from app.services.data.eod_incremental import incremental_sync_eod
    from app.services.data.sync_progress import init_progress, update_progress, clear_progress

    days = req.days or 30  # 默认拉取30天
    init_progress(universe, "akshare", total_mb=0)
    update_progress(pct=0, status="running", message=f"正在通过akshare拉取{universe}最近{days}天EOD数据...")

    try:
        result = await incremental_sync_eod(universe=universe, days=days)

        if result.get("ok"):
            success = result.get("success", 0)
            failed = result.get("failed", 0)
            new_dates = result.get("new_dates", [])

            if success == 0:
                update_progress(pct=100, status="failed",
                    error=f"全部股票拉取失败（共{result.get('total_stocks', 0)}只）")
                raise ValueError(f"akshare拉取全部失败，可能被反爬。建议检查网络或使用chenditc数据源")

            update_progress(pct=100, status="done",
                message=f"同步完成：成功{success}只，失败{failed}只，新增{len(new_dates)}个交易日")

            # 从 qlib bin 目录采集准确统计（不依赖 new_dates）
            stats = _collect_qlib_stats(settings.qlib_provider_path)
            return {
                "universe": universe,
                "data_source": "akshare",
                "days": days,
                "success": success,
                "failed": failed,
                "new_dates": new_dates,
                "total_stocks": result.get("total_stocks", 0),
                "latest_date": stats["latest_date"],
                "stock_count": stats["stock_count"],
                "row_count": stats["row_count"],
                "qlib_dir": settings.qlib_provider_path,
            }
        else:
            error = result.get("error", "未知错误")
            update_progress(pct=100, status="failed", error=error)
            raise ValueError(error)
    except Exception as e:
        update_progress(pct=100, status="failed", error=str(e))
        raise
    finally:
        clear_progress()


async def _run_sync_task(req: SyncDataRequest):
    """后台执行数据同步（独立 session）。

    根据 config.quant.data_source 选择数据源：
      - chenditc（默认）：下载预构建 tarball
      - akshare         ：逐只爬取（兜底）
    """
    universe = req.universe or settings.quant.get("universe", "csi300")
    data_source = settings.quant.get("data_source", "chenditc")
    period = settings.quant.get("default_backtest_period", {})
    start_date = req.start_date or period.get("start", "2020-01-01")
    end_date = req.end_date or datetime.now().strftime("%Y-%m-%d")

    logger.info("开始数据同步 universe=%s data_source=%s", universe, data_source)

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
        from app.services.quant.qlib_init import QlibNotAvailableError
        if data_source == "chenditc":
            summary = await _sync_via_chenditc(universe)
        else:
            summary = await _sync_via_akshare(universe, req)

        async with async_session() as session:
            existing = await session.execute(
                select(StockDataStatus).where(StockDataStatus.universe == universe)
            )
            rec = existing.scalar_one_or_none()
            if rec is None:
                rec = StockDataStatus(universe=universe)
                session.add(rec)
            # chenditc 提供精确统计；akshare 用 done 计数 + end_date
            rec.latest_date = summary.get("latest_date") or summary.get("end_date")
            rec.stock_count = summary.get("stock_count", summary.get("done", 0))
            rec.row_count = summary.get("row_count", summary.get("done", 0))
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


def _classify_sync_error(error: str) -> dict:
    """根据错误信息分类失败原因，返回分类标签与建议解决方案。

    用于向前端返回更友好的错误信息，区分网络/磁盘/数据损坏/中断等。
    """
    err = (error or "").lower()
    if any(k in err for k in (
        "connection aborted", "remotedisconnected", "connectionerror",
        "timeout", "timed out", "ssl", "urlopen", "max retries", "network",
        "proxy", "connection reset",
    )):
        return {
            "category": "network",
            "category_label": "网络错误",
            "suggestion": "请检查网络连接后重试。GitHub Releases 在国内访问不稳定，建议配置代理或多次重试（已内置指数退避重试）。",
        }
    if any(k in err for k in ("no space left", "enospc", "disk", "磁盘空间不足", "quota")):
        return {
            "category": "disk_full",
            "category_label": "磁盘空间不足",
            "suggestion": "磁盘空间不足，请清理临时文件或扩容后重试（qlib_bin.tar.gz 约 500MB+，解压后需 2GB+ 空间）。",
        }
    if any(k in err for k in (
        "corrupt", "checksum", "tarfile", "readerror", "解压", "day.txt",
        "数据可能不完整", "bad tarfile", "not a gzip",
    )):
        return {
            "category": "data_corrupt",
            "category_label": "数据损坏",
            "suggestion": "下载数据可能损坏，建议删除临时文件后重新同步。",
        }
    if any(k in err for k in ("container restart", "interrupted", "sync timeout", "同步超时")):
        return {
            "category": "interrupted",
            "category_label": "同步被中断",
            "suggestion": "同步过程被中断，建议重试同步（可开启自动重试：config.quant.auto_retry_sync=true）。",
        }
    if "csv" in err or ("拉取" in err and "失败" in err):
        return {
            "category": "fetch_failed",
            "category_label": "数据拉取失败",
            "suggestion": "akshare数据源可能被反爬或网络不稳定。建议：1.检查网络连接 2.使用增量同步拉取较少天数 3.切换到chenditc数据源",
        }
    return {
        "category": "unknown",
        "category_label": "未知错误",
        "suggestion": "请查看后端日志排查，或重试同步。",
    }


async def _mark_failed(universe: str, error: str):
    """标记同步失败，并在 last_error 中附上失败分类与建议解决方案。"""
    cls = _classify_sync_error(error)
    friendly = "[{label}] {err}\n建议: {sug}".format(
        label=cls["category_label"], err=error[:400], sug=cls["suggestion"]
    )
    logger.error(
        "数据同步失败 universe=%s category=%s: %s",
        universe, cls["category"], error[:500],
    )
    async with async_session() as session:
        existing = await session.execute(
            select(StockDataStatus).where(StockDataStatus.universe == universe)
        )
        rec = existing.scalar_one_or_none()
        if rec is None:
            rec = StockDataStatus(universe=universe)
            session.add(rec)
        rec.status = "failed"
        rec.last_error = friendly[:500]
        rec.last_updated = datetime.now()
        await session.commit()


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

    数据源由 config.quant.data_source 决定（默认 chenditc，可选 akshare）。
    """
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError(
            "QLIB_NOT_AVAILABLE",
            "qlib 未安装，无法同步数据。请在 Python 3.11 环境安装 pyqlib 后重试",
            503,
        )

    universe = req.universe or settings.quant.get("universe", "csi300")
    data_source = settings.quant.get("data_source", "chenditc")
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
