"""定时任务调度。"""
import logging

logger = logging.getLogger(__name__)


def register_scheduled_jobs(scheduler):
    """注册定时任务。

    注意：数据同步改为手动触发（baostock 回填），不再注册每日自动同步任务。
    """

    def _spawn_decay_check():
        # 衰减检测为 CPU 密集长任务，跑独立子进程（factor_monitor_worker），
        # 不占 web 进程线程池，uvicorn --reload 关停时也不会等它。
        # 已有 worker 在跑则跳过（防止 18:05 重触发叠加）。
        from app.services.quant.factor_monitor_worker import (
            is_decay_check_running,
            spawn_decay_check_worker,
        )
        if is_decay_check_running():
            import logging
            logging.getLogger(__name__).warning("衰减检测已在执行中，跳过本次定时触发")
            return
        spawn_decay_check_worker()

    # 每工作日 18:05 检测因子衰减（错开 5 分钟避免与数据同步抢资源）
    scheduler.add_job(
        _spawn_decay_check, "cron",
        hour=18, minute=5, day_of_week="mon-fri",
        id="factor_decay_check", replace_existing=True,
        name="因子衰减检测（子进程）",
    )
    # 每 10 分钟回收僵尸挖掘任务（运行超时仍卡 running 的）
    from app.core.recovery import reap_stale_mining
    scheduler.add_job(
        reap_stale_mining, "interval", minutes=10,
        id="reap_stale_mining", replace_existing=True,
        name="僵尸挖掘任务回收",
    )
    # 定时数据刷新：每分钟 tick，配置（SyncSchedule）在 tick 内比对（启用/时间/工作日/当日幂等）
    from app.services.task.sync_schedule_service import tick_scheduled_sync
    scheduler.add_job(
        tick_scheduled_sync, "interval", minutes=1,
        id="scheduled_data_refresh", replace_existing=True,
        name="定时数据刷新（政策/行情）",
    )
    # 定时数据管理同步：每分钟 tick，配置（DataSyncSchedule）在 tick 内比对
    from app.services.task.data_sync_schedule_service import tick_scheduled_data_sync
    scheduler.add_job(
        tick_scheduled_data_sync, "interval", minutes=1,
        id="scheduled_data_sync", replace_existing=True,
        name="定时数据同步（数据管理）",
    )
