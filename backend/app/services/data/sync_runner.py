"""数据同步执行器（service 层）。

将原位于 api 层的同步编排逻辑下沉至此，消除 core.recovery -> api.quant_data 的反向依赖。
api 层与 recovery 层均通过本模块触发后台同步。
"""
import logging
from datetime import datetime
from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session
from app.models.stock_data_status import StockDataStatus
from app.schemas.quant import SyncDataRequest

logger = logging.getLogger(__name__)


def collect_qlib_stats(qlib_dir: str) -> dict:
    """从解压后的 qlib bin 目录统计 latest_date / stock_count / row_count。"""
    from pathlib import Path
    base = Path(qlib_dir)
    latest_date = None
    day_txt = base / "calendars" / "day.txt"
    if day_txt.exists():
        lines = [l.strip() for l in day_txt.read_text(encoding="utf-8").splitlines() if l.strip()]
        if lines:
            latest_date = lines[-1]
    inst_dir = base / "instruments"
    stock_count = 0
    row_count = 0
    if inst_dir.exists():
        for txt in sorted(inst_dir.glob("*.txt")):
            n = sum(1 for l in txt.read_text(encoding="utf-8").splitlines() if l.strip())
            row_count += n
            if txt.name == "all.txt":
                stock_count = n
    return {"latest_date": latest_date, "stock_count": stock_count, "row_count": row_count}


def classify_sync_error(error: str) -> dict:
    """根据错误信息分类失败原因，返回分类标签与建议解决方案。"""
    err = (error or "").lower()
    if any(k in err for k in (
        "connection aborted", "remotedisconnected", "connectionerror",
        "timeout", "timed out", "ssl", "urlopen", "max retries", "network",
        "proxy", "connection reset",
    )):
        return {
            "category": "network", "category_label": "网络错误",
            "suggestion": "请检查网络连接后重试。GitHub Releases 在国内访问不稳定，建议配置代理或多次重试（已内置指数退避重试）。",
        }
    if any(k in err for k in ("no space left", "enospc", "disk", "磁盘空间不足", "quota")):
        return {
            "category": "disk_full", "category_label": "磁盘空间不足",
            "suggestion": "磁盘空间不足，请清理临时文件或扩容后重试（qlib_bin.tar.gz 约 500MB+，解压后需 2GB+ 空间）。",
        }
    if any(k in err for k in (
        "corrupt", "checksum", "tarfile", "readerror", "解压", "day.txt",
        "数据可能不完整", "bad tarfile", "not a gzip",
    )):
        return {
            "category": "data_corrupt", "category_label": "数据损坏",
            "suggestion": "下载数据可能损坏，建议删除临时文件后重新同步。",
        }
    if any(k in err for k in ("container restart", "interrupted", "sync timeout", "同步超时")):
        return {
            "category": "interrupted", "category_label": "同步被中断",
            "suggestion": "同步过程被中断，建议重试同步（可开启自动重试：config.quant.auto_retry_sync=true）。",
        }
    if "csv" in err or ("拉取" in err and "失败" in err):
        return {
            "category": "fetch_failed", "category_label": "数据拉取失败",
            "suggestion": "akshare数据源可能被反爬或网络不稳定。建议：1.检查网络连接 2.使用增量同步拉取较少天数 3.切换到chenditc数据源",
        }
    return {"category": "unknown", "category_label": "未知错误", "suggestion": "请查看后端日志排查，或重试同步。"}


async def mark_sync_failed(universe: str, error: str):
    """标记同步失败，并在 last_error 中附上失败分类与建议解决方案。"""
    cls = classify_sync_error(error)
    friendly = "[{label}] {err}\n建议: {sug}".format(
        label=cls["category_label"], err=error[:400], sug=cls["suggestion"]
    )
    logger.error("数据同步失败 universe=%s category=%s: %s", universe, cls["category"], error[:500])
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


async def _sync_via_chenditc(universe: str) -> dict:
    """通过 chenditc 预构建 tarball 同步 qlib bin 数据。"""
    import asyncio
    from app.services.data.chenditc_client import download_qlib_bin, get_latest_release_info
    from app.services.data.sync_progress import init_progress, finish_progress, clear_progress
    from app.models.sync_history import SyncHistory

    qlib_dir = settings.qlib_provider_path
    release = get_latest_release_info()
    total_mb = release.get("size", 0) / 1024 / 1024 if "error" not in release else 0
    init_progress(universe, "chenditc", total_mb=total_mb)

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
        stats = collect_qlib_stats(qlib_dir)
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
        from app.core.websocket_manager import ws_manager
        await ws_manager.broadcast("sync_complete", {
            "universe": universe, "latest_date": stats["latest_date"], "stock_count": stats["stock_count"],
        })
    except Exception as e:
        finish_progress(False, str(e))
        async with async_session() as session:
            h = await session.get(SyncHistory, history_id)
            if h:
                h.status = "failed"
                h.finished_at = datetime.now()
                h.error = str(e)[:500]
                await session.commit()
        clear_progress()
        raise

    stats = collect_qlib_stats(qlib_dir)
    logger.info(
        "chenditc 同步完成 universe=%s version=%s latest_date=%s stocks=%d rows=%d",
        universe, result.get("version"), stats["latest_date"], stats["stock_count"], stats["row_count"],
    )
    return {
        "universe": universe, "latest_date": stats["latest_date"],
        "stock_count": stats["stock_count"], "row_count": stats["row_count"],
        "qlib_dir": qlib_dir, "version": result.get("version"),
        "release_date": result.get("date"), "data_source": "chenditc",
    }


async def _incremental_sync(req: SyncDataRequest, universe: str, source: str, days: int) -> dict:
    """通过指定数据源（baostock/akshare）增量同步 EOD 数据。

    依赖契约（并行开发中）:
        app.services.data.eod_incremental.incremental_sync_eod
            (days, universe, source, overwrite) -> dict  # 同步函数，在进程池中执行

    Args:
        req: 同步请求（保留以兼容调用方）
        universe: 股票池
        source: 数据源 'baostock'(一次拉全市场) / 'akshare'(逐只爬)
        days: 回溯天数

    Returns:
        dict: 含 latest_date/stock_count/row_count/qlib_dir/data_source 等字段的摘要
    """
    from app.services.data.eod_incremental import incremental_sync_eod
    from app.services.data.sync_progress import init_progress, update_progress, clear_progress
    from app.core.executor import run_cpu

    init_progress(universe, source, total_mb=0)
    update_progress(pct=0, status="running",
                    message=f"正在通过{source}拉取{universe}最近{days}天EOD数据...")
    try:
        # 在 CPU 进程池中执行同步函数（CPU密集+IO，避免阻塞事件循环）
        result = await run_cpu(incremental_sync_eod,
                               days=days, universe=universe, source=source)
        if result.get("ok"):
            success = result.get("success", 0)
            failed = result.get("failed", 0)
            new_dates = result.get("new_dates", [])
            if success == 0:
                update_progress(pct=100, status="failed",
                                error=f"全部股票拉取失败（共{result.get('total_stocks', 0)}只）")
                raise ValueError(f"{source}拉取全部失败，可能被反爬或数据源不可用")
            update_progress(pct=100, status="done",
                            message=f"同步完成：成功{success}只，失败{failed}只，新增{len(new_dates)}个交易日")
            stats = collect_qlib_stats(settings.qlib_provider_path)
            return {
                "universe": universe, "data_source": source, "days": days,
                "success": success, "failed": failed, "new_dates": new_dates,
                "total_stocks": result.get("total_stocks", 0),
                "latest_date": stats["latest_date"], "stock_count": stats["stock_count"],
                "row_count": stats["row_count"], "qlib_dir": settings.qlib_provider_path,
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


async def _try_baostock_incremental(req: SyncDataRequest) -> dict:
    """用 baostock 做增量同步（一次拉全市场，推荐兜底源）。

    依赖 baostock_client.fetch_daily_all_a_stock_sync（并行开发中）。
    """
    universe = req.universe or "all"  # baostock 一次拉全市场
    days = req.days or 5
    return await _incremental_sync(req, universe, "baostock", days)


async def _try_akshare_incremental(req: SyncDataRequest) -> dict:
    """用 akshare 逐只增量同步（慢，仅个股/指数兜底）。"""
    universe = req.universe or settings.quant.get("universe", "csi300")
    days = req.days or 30
    return await _incremental_sync(req, universe, "akshare", days)


async def _dispatch_sync(req: SyncDataRequest, universe: str, source: str) -> dict:
    """按数据源分发同步，chenditc 失败回退 baostock 增量（三层回退链）。

    回退链（source='chenditc' 默认）:
        1. chenditc 全量（预构建 tarball，原路径保留）
        2. baostock 增量（一次拉全市场，比 akshare 逐只快且不丢字段）
    source='baostock': 直接 baostock 增量
    source='akshare': akshare 逐只增量

    Returns:
        dict: 同步摘要（含 latest_date/stock_count/row_count/qlib_dir/data_source）
    Raises:
        Exception: 所有回退层级均失败时抛出，由 run_sync_task 捕获并标记 failed
    """
    if source == "chenditc":
        # 1. chenditc 全量（原路径，保留）
        try:
            return await _sync_via_chenditc(universe)
        except Exception as e:
            logger.warning("chenditc全量失败: %s，回退 baostock 增量", e)
        # 2. baostock 增量兜底（一次拉全市场，比 akshare 逐只快且不丢字段）
        try:
            result = await _try_baostock_incremental(req)
            result["fallback"] = "baostock"
            logger.info("已回退至 baostock 增量同步")
            return result
        except Exception as e:
            logger.error("baostock 增量也失败: %s", e)
            raise

    if source == "baostock":
        return await _try_baostock_incremental(req)

    # akshare（兜底）
    return await _try_akshare_incremental(req)


async def run_sync_task(req: SyncDataRequest):
    """后台执行数据同步（独立 session）— 三层回退链。

    根据 config.quant.data_source 选择数据源：
      - chenditc（默认）：chenditc 全量 → 失败回退 baostock 增量
      - baostock：直接 baostock 增量
      - akshare：akshare 逐只增量（兜底）

    签名向后兼容：仅接收 req，无返回值（后台任务）。
    """
    universe = req.universe or settings.quant.get("universe", "csi300")
    data_source = settings.quant.get("data_source", "chenditc")

    logger.info("开始数据同步 universe=%s data_source=%s", universe, data_source)

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
        summary = await _dispatch_sync(req, universe, data_source)

        async with async_session() as session:
            existing = await session.execute(
                select(StockDataStatus).where(StockDataStatus.universe == universe)
            )
            rec = existing.scalar_one_or_none()
            if rec is None:
                rec = StockDataStatus(universe=universe)
                session.add(rec)
            # 仅当统计到真实数据日期才更新；避免回退到 end_date(=今天)
            # 误把"同步执行日"当成"数据日期"（昨天的数据今天同步不应算到今天）
            new_latest = summary.get("latest_date")
            if new_latest:
                rec.latest_date = new_latest
            rec.stock_count = summary.get("stock_count", summary.get("done", 0))
            rec.row_count = summary.get("row_count", summary.get("done", 0))
            rec.status = "ok"
            rec.last_error = None
            rec.qlib_dir = summary.get("qlib_dir")
            rec.last_updated = datetime.now()
            await session.commit()
    except QlibNotAvailableError as e:
        await mark_sync_failed(universe, str(e))
        logger.error("qlib 不可用: %s", e)
    except Exception as e:
        await mark_sync_failed(universe, str(e))
        logger.exception("数据同步失败")
