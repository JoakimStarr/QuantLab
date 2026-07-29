import logging
from datetime import datetime
from sqlalchemy import select

logger = logging.getLogger(__name__)


def _auto_retry_enabled() -> bool:
    """是否开启卡死同步的自动重试。

    开关：config.quant.auto_retry_sync 或环境变量 QUANTLAB_AUTO_RETRY_SYNC。
    """
    enabled = False
    try:
        from app.core.config import settings
        enabled = bool(settings.quant.get("auto_retry_sync", False))
    except Exception:
        pass
    import os
    env_flag = os.environ.get("QUANTLAB_AUTO_RETRY_SYNC", "").lower() in ("1", "true", "yes")
    return enabled or env_flag


async def recover_stale_sync():
    """启动时恢复卡死的同步任务。

    将所有 status=syncing 的记录标记为 failed（容器重启中断的同步），
    记录详细的恢复日志（universe / 上次更新时间 / 中断时长），
    并在开启自动重试时对新失败的任务触发后台重试。
    """
    from app.core.database import async_session
    from app.models.stock_data_status import StockDataStatus

    now = datetime.now()
    recovered = []
    async with async_session() as session:
        result = await session.execute(
            select(StockDataStatus).where(StockDataStatus.status == "syncing")
        )
        for rec in result.scalars().all():
            prev_updated = rec.last_updated
            stale_seconds = (now - prev_updated).total_seconds() if prev_updated else None
            rec.status = "failed"
            rec.last_error = "container restart interrupted sync"
            rec.last_updated = now
            logger.warning(
                "recover: universe=%s syncing->failed (last_updated=%s, stale=%.0fs)",
                rec.universe,
                prev_updated.isoformat() if prev_updated else "unknown",
                stale_seconds if stale_seconds is not None else -1,
            )
            recovered.append(rec.universe)
        await session.commit()

    if not recovered:
        logger.info("recover: 无卡死同步任务")
        return

    logger.info("recover: 共恢复 %d 个卡死同步: %s", len(recovered), recovered)

    if _auto_retry_enabled():
        logger.info("recover: auto_retry 已开启，开始自动重试: %s", recovered)
        try:
            await _auto_retry_sync(recovered)
        except Exception as e:
            logger.error("recover: 自动重试触发失败: %s", e)
    else:
        logger.info(
            "recover: auto_retry 未开启，跳过自动重试 "
            "(设置 config.quant.auto_retry_sync=true 或 QUANTLAB_AUTO_RETRY_SYNC=1 开启)"
        )


async def recover_stale_mining():
    """启动时恢复卡死的因子挖掘任务。

    BackgroundTasks 是进程内任务，服务重启会丢失，但数据库中 status 仍为
    running/pending。将这些僵尸记录标记为 failed，避免前端永久显示"运行中"。
    """
    from app.core.database import async_session
    from app.models.mining_task import MiningTask

    now = datetime.now()
    recovered = []
    async with async_session() as session:
        result = await session.execute(
            select(MiningTask).where(MiningTask.status.in_(["running", "pending"]))
        )
        for rec in result.scalars().all():
            prev_started = rec.started_at
            stale_seconds = (now - prev_started).total_seconds() if prev_started else None
            rec.status = "failed"
            rec.error = "container restart interrupted mining (zombie recovered)"
            rec.finished_at = now
            logger.warning(
                "recover mining: task_id=%s type=%s %s->failed (started=%s, stale=%.0fs)",
                rec.id,
                rec.type,
                rec.status,
                prev_started.isoformat() if prev_started else "unknown",
                stale_seconds if stale_seconds is not None else -1,
            )
            recovered.append(rec.id)
        await session.commit()

    if not recovered:
        logger.info("recover mining: 无卡死挖掘任务")
    else:
        logger.info("recover mining: 共恢复 %d 个卡死挖掘任务: %s", len(recovered), recovered)


async def _auto_retry_sync(universes: list):
    """对被中断的 universe 列表自动触发后台重试同步。"""
    import asyncio
    from app.schemas.quant import SyncDataRequest

    # 延迟导入，避免 core -> api 循环依赖
    from app.api.quant_data import _run_sync_task

    for universe in universes:
        logger.info("recover: 自动重试 universe=%s", universe)
        req = SyncDataRequest(universe=universe)
        asyncio.create_task(_run_sync_task(req))
