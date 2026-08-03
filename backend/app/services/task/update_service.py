"""定时任务调度。"""
import logging

logger = logging.getLogger(__name__)


def register_scheduled_jobs(scheduler):
    """注册定时任务。

    注意：数据同步改为手动触发（baostock 回填），不再注册每日自动同步任务。
    """
    # 每工作日 18:05 检测因子衰减（错开 5 分钟避免与数据同步抢资源）
    from app.services.quant.factor_monitor import run_decay_check
    scheduler.add_job(
        run_decay_check, "cron",
        hour=18, minute=5, day_of_week="mon-fri",
        id="factor_decay_check", replace_existing=True,
        name="因子衰减检测",
    )
    # 每 10 分钟回收僵尸挖掘任务（运行超时仍卡 running 的）
    from app.core.recovery import reap_stale_mining
    scheduler.add_job(
        reap_stale_mining, "interval", minutes=10,
        id="reap_stale_mining", replace_existing=True,
        name="僵尸挖掘任务回收",
    )
