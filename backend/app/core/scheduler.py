import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, update

from app.core.database import async_session
from app.models.backtest_result import BacktestResult
from app.models.sync_history import SyncHistory

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")


async def cleanup_task():
    """数据归档清理任务：每周日凌晨 3:00 执行。

    清理规则：
    - backtest_result：90 天前的数据（软删除）
    - sync_history：365 天前的数据（硬删除）
    """
    logger.info("数据归档清理任务开始")
    now = datetime.now()

    # 1. 软删除 90 天前的 backtest_result
    try:
        cutoff_90 = now - timedelta(days=90)
        async with async_session() as session:
            # 软删除 backtest_result
            await session.execute(
                update(BacktestResult)
                .where(BacktestResult.created_at < cutoff_90)
                .where(BacktestResult.is_deleted == 0)
                .values(is_deleted=1, deleted_at=now)
            )
            await session.commit()
            logger.info("backtest_result 软删除完成（截止 %s）", cutoff_90)
    except Exception as e:
        logger.error("backtest_result 清理失败: %s", e)

    # 2. 硬删除 365 天前的 sync_history
    try:
        cutoff_365 = now - timedelta(days=365)
        async with async_session() as session:
            result = await session.execute(
                select(SyncHistory).where(SyncHistory.started_at < cutoff_365)
            )
            rows = result.scalars().all()
            for row in rows:
                await session.delete(row)
            await session.commit()
            logger.info("sync_history 硬删除完成，共删除 %d 条记录（截止 %s）", len(rows), cutoff_365)
    except Exception as e:
        logger.error("sync_history 清理失败: %s", e)

    logger.info("数据归档清理任务完成")


async def log_cleanup_task():
    """日志文件定期清理：每天 03:30 删除过期的轮转备份。

    规则（config.logging）：
    - quantlab.log / sync.log 的轮转备份保留 retention_days 天（默认 7）
    - error.log 的轮转备份保留 error_retention_days 天（默认 15，更长），
      保证普通日志清理后仍可定位历史错误
    """
    from app.core.config import settings
    from app.core.logging_config import cleanup_old_logs

    if not settings.logging.cleanup_enabled:
        return
    import asyncio

    result = await asyncio.to_thread(
        cleanup_old_logs, None,
        settings.logging.retention_days,
        settings.logging.error_retention_days,
    )
    logger.info("日志清理完成: 删除 %d 个，释放 %.2f MB",
                result["deleted_count"], result["freed_bytes"] / 1048576.0)


async def start_scheduler():
    from app.services.task.update_service import register_scheduled_jobs
    register_scheduled_jobs(scheduler)
    # 数据库归档清理：每周日凌晨 3:00
    scheduler.add_job(
        cleanup_task, "cron",
        hour=3, minute=0, day_of_week="sun",
        id="data_cleanup", replace_existing=True,
        name="数据库归档清理",
    )
    # 日志文件定期清理：每天 03:30（普通日志 7 天 / 错误日志 15 天）
    scheduler.add_job(
        log_cleanup_task, "cron",
        hour=3, minute=30,
        id="log_cleanup", replace_existing=True,
        name="日志文件定期清理",
    )
    scheduler.start()


async def stop_scheduler():
    scheduler.shutdown(wait=True)
