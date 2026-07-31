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
    from app.services.data.sync_runner import run_sync_task

    pending = []
    for universe in universes:
        logger.info("recover: 自动重试 universe=%s", universe)
        req = SyncDataRequest(universe=universe)
        t = asyncio.create_task(run_sync_task(req))
        # 保留引用并记录异常，避免任务异常被静默吞没
        t.add_done_callback(
            lambda fut: fut.exception() and logger.error("recover: 自动重试失败: %s", fut.exception())
        )
        pending.append(t)
    return pending


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
    """
    from datetime import datetime, timedelta
    from sqlalchemy import select
    from app.core.database import async_session
    from app.models.mining_task import MiningTask
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
                # 重置 running -> pending，清理错误信息
                t.status = "pending"
                t.error = None
                t.started_at = None
            await session.commit()
            # 重新提交（延迟导入避免循环依赖）
            for t in tasks:
                await _resubmit_mining(t.id, t.type, t.params)
    except Exception:
        logger.exception("重跑未完成任务失败")


async def _resubmit_mining(task_id: int, task_type: str, params_json: str) -> None:
    """重新提交挖掘任务到 BackgroundTasks 等价通道。

    复用 mining API 的执行逻辑：通过 asyncio.create_task 在后台跑。
    """
    import json
    import asyncio
    from app.services.mining.task_utils import update_task_status
    try:
        params = json.loads(params_json) if params_json else {}
    except Exception:
        params = {}
    # 根据类型派发（与 api/mining.py 保持一致的入口）
    async def _run():
        try:
            if task_type == "llm":
                from app.services.mining.llm_factor import mine_with_llm_iterative
                n_rounds = params.get("n_rounds", 1)
                n_candidates = params.get("n_candidates")
                await mine_with_llm_iterative(task_id, n_rounds=n_rounds, n_candidates=n_candidates)
            elif task_type == "symbolic":
                from app.services.mining.symbolic import mine_with_symbolic
                await mine_with_symbolic(task_id, **{k: v for k, v in params.items() if k in ("n_candidates", "ic_threshold")})
            elif task_type == "automl":
                from app.services.mining.automl import mine_with_automl
                await mine_with_automl(task_id, **{k: v for k, v in params.items() if k in ("factor_ids", "method")})
            elif task_type == "text":
                from app.services.mining.text_factor import mine_with_text
                await mine_with_text(task_id, **{k: v for k, v in params.items() if k in ("max_news_per_day",)})
            else:
                logger.warning("未知任务类型 %s，跳过 task_id=%s", task_type, task_id)
        except Exception as e:
            logger.exception("重跑任务失败 task_id=%s", task_id)
            await update_task_status(task_id, status="failed", error=str(e)[:500])
    asyncio.create_task(_run())
