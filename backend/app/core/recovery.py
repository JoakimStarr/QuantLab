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


def _live_worker_for(universe: str) -> bool:
    """该 universe 是否有真正存活的同步 worker 在跑。

    同步 worker 跑在独立进程组（start_new_session），web 进程重启/重载不会杀它。
    recover_stale_sync 只在 web 启动时运行，若 worker 仍在跑却把 DB 状态标成 failed，
    会导致前端读 DB 看不到 syncing（进度条不显示），而 sync_is_active()（读进度文件）
    仍会阻塞其他同步 → 状态永久错位。因此标记前必须先确认 worker 已死。
    """
    from app.services.data.sync_progress import get_progress, sync_is_active

    prog = get_progress()
    if not prog:
        return False
    if prog.get("universe") != universe:
        return False
    return sync_is_active()


async def recover_stale_sync():
    """启动时恢复卡死的同步任务。

    将所有 status=syncing 的记录标记为 failed（容器重启中断的同步），
    但**跳过仍有存活 worker 的记录**（独立进程组里的 worker 不受 web 重启影响）。
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
            if _live_worker_for(rec.universe):
                logger.info(
                    "recover: universe=%s 的同步 worker 仍存活，跳过恢复（保留 syncing）",
                    rec.universe,
                )
                continue
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

    挖掘任务现跑在独立子进程（mining_worker，start_new_session），web 重启不会杀它。
    因此这里**只把 running 标为 failed**，且标记前先确认 worker 已死（读 PID 标记文件）
    ——若 worker 仍在跑却把 DB 标成 failed，前端会看到"失败"而子进程还在继续写库，
    状态永久错位。pending 保留给 rerun_pending_mining 重跑（若在此也标 failed，
    rerun 会查不到 pending 任务，自动重跑机制被抵消）。
    """
    from app.core.database import async_session
    from app.models.mining_task import MiningTask
    from app.services.mining.mining_worker import is_mining_worker_alive

    now = datetime.now()
    recovered = []
    async with async_session() as session:
        result = await session.execute(
            select(MiningTask).where(MiningTask.status == "running")
        )
        for rec in result.scalars().all():
            if is_mining_worker_alive(rec.id):
                logger.info(
                    "recover mining: task_id=%s 的 worker 仍存活，跳过恢复（保留 running）",
                    rec.id,
                )
                continue
            prev_started = rec.started_at
            stale_seconds = (now - prev_started).total_seconds() if prev_started else None
            rec.status = "failed"
            rec.error = "container restart interrupted mining (zombie recovered)"
            rec.finished_at = now
            logger.warning(
                "recover mining: task_id=%s type=%s running->failed (started=%s, stale=%.0fs)",
                rec.id,
                rec.type,
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
    """对被中断的 universe 列表自动触发后台重试同步（baostock 回填）。

    自动重试标记为 sync_trigger='auto'，允许 30 分钟超时回收，
    避免手动同步与自动重试混为一谈导致误杀。

    同步通过独立 worker 子进程执行（app.services.data.sync_worker），
    与 web 进程解耦：进程重启不会中断正在进行的回填。
    """
    import asyncio
    from datetime import datetime
    from app.core.database import async_session
    from app.models.stock_data_status import StockDataStatus

    # 先统一标记为 syncing（auto 触发），供 _detect_stale_sync 超时回收
    async with async_session() as session:
        for universe in universes:
            existing = await session.execute(
                select(StockDataStatus).where(StockDataStatus.universe == universe)
            )
            rec = existing.scalar_one_or_none()
            if rec is None:
                rec = StockDataStatus(universe=universe)
                session.add(rec)
            rec.status = "syncing"
            rec.sync_trigger = "auto"
            rec.last_error = None
            rec.last_updated = datetime.now()
        await session.commit()

    from app.services.data.sync_worker import spawn_sync_worker
    for universe in universes:
        logger.info("recover: 自动重试 universe=%s", universe)
        spawn_sync_worker("backfill", universe)


async def reap_stale_mining():
    """运行时回收僵尸挖掘任务：running 状态超过最大超时 2 倍的标记为 failed。

   弥补 _safe_run_task 超时对纯同步阻塞调用可能失效的场景，避免任务永久卡 running。
    """
    from datetime import timedelta
    from app.core.database import async_session
    from app.core.config import settings
    from app.models.mining_task import MiningTask

    task_cfg = settings.task or {}
    timeouts = task_cfg.get("timeouts", {}) or {}
    base = int(task_cfg.get("task_timeout_seconds", 300))
    max_timeout = max([int(v) for v in timeouts.values()] + [base]) * 2
    threshold = datetime.now() - timedelta(seconds=max_timeout)

    async with async_session() as session:
        result = await session.execute(
            select(MiningTask).where(
                MiningTask.status == "running",
                MiningTask.started_at < threshold,
            )
        )
        stale = result.scalars().all()
        for rec in stale:
            rec.status = "failed"
            rec.error = f"运行超时被 reaper 回收 (>{max_timeout}s)"
            rec.finished_at = datetime.now()
            logger.warning("reaper: 回收僵尸挖掘任务 task_id=%s type=%s", rec.id, rec.type)
        if stale:
            await session.commit()
            logger.info("reaper: 共回收 %d 个僵尸挖掘任务", len(stale))


async def rerun_pending_mining() -> None:
    """重启后重跑状态为 pending/running 的挖掘任务（进程崩溃恢复）。

    策略：running 视为崩溃残留，重置为 pending 后重新提交；
    pending 直接重新提交。仅重试近 N 天的任务避免无限堆积。
    跳过仍有存活 worker 的任务（web 重启但 worker 在跑，不重复 spawn）。
    """
    from datetime import datetime, timedelta
    from sqlalchemy import select
    from app.core.database import async_session
    from app.models.mining_task import MiningTask
    from app.services.mining.mining_worker import is_mining_worker_alive
    try:
        async with async_session() as session:
            cutoff = datetime.now() - timedelta(days=3)
            stmt = select(MiningTask).where(
                MiningTask.status.in_(["pending", "running"]),
                MiningTask.created_at >= cutoff,
            )
            result = await session.execute(stmt)
            tasks = result.scalars().all()
            if not tasks:
                return
            logger.info("恢复 %d 个未完成挖掘任务", len(tasks))
            for t in tasks:
                if is_mining_worker_alive(t.id):
                    logger.info("rerun mining: task_id=%s 的 worker 仍存活，跳过重跑", t.id)
                    continue
                # 重置 running -> pending，清理错误信息
                t.status = "pending"
                t.error = None
                t.started_at = None
            await session.commit()
            # 重新提交（延迟导入避免循环依赖）
            for t in tasks:
                if is_mining_worker_alive(t.id):
                    continue
                await _resubmit_mining(t.id, t.type, t.params)
    except Exception:
        logger.exception("重跑未完成任务失败")


async def _resubmit_mining(task_id: int, task_type: str, params_json: str) -> None:
    """重新提交挖掘任务到独立 worker 子进程。

    复用 mining API 的入口：spawn_mining_worker 启动子进程执行。
    """
    import json
    from app.services.mining.mining_worker import spawn_mining_worker
    try:
        params = json.loads(params_json) if params_json else {}
    except Exception:
        params = {}
    try:
        spawn_mining_worker(task_id, task_type, params)
    except Exception as e:
        logger.exception("重跑任务 spawn 失败 task_id=%s", task_id)
        from app.services.mining.task_utils import update_task_status
        await update_task_status(task_id, status="failed", error=str(e)[:500])
