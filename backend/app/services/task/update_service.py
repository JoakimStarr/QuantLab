"""定时任务调度：qlib 股票数据增量同步。"""
import asyncio
import logging
from datetime import datetime

from app.core.config import settings
from app.core.database import async_session
from app.models.stock_data_status import StockDataStatus

logger = logging.getLogger(__name__)


async def daily_quant_data_update():
    """每日同步 qlib 股票数据（工作日 18:00），含失败重试。

    触发逻辑优化（替代旧的"增量失败直接全量"）：
      1. 走 smart_sync 编排：按 latest_date 距今天数自动选择路径
         - 距今 0 天: baostock 同步当日
         - 距今 1-7 天: baostock 增量补缺失日期
         - 距今 > 7 天 或日历缺失: chenditc 全量
      2. 失败按错误分类决定重试策略：
         - 网络错误: 指数退避重试（最多 3 次）
         - 数据损坏: 直接全量重建（不重试增量）
         - 其他: 重试 1 次后放弃
      3. 通过 StockDataStatus.last_sync_path 记录上次成功路径，
         增量失败时若上次成功路径也是增量，降级全量；否则保持原路径重试
    """
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        logger.info("qlib 不可用，跳过股票数据同步")
        return

    from app.services.data.smart_sync import smart_sync
    from app.services.data.sync_runner import classify_sync_error

    universe = settings.quant.get("universe", "csi300")
    max_retries = 3
    backoff_seconds = [60, 300, 600]  # 1min / 5min / 10min

    for attempt in range(1, max_retries + 1):
        try:
            logger.info("定时数据同步启动 universe=%s (attempt %d/%d)",
                        universe, attempt, max_retries)
            # smart_sync 内部根据 latest_date 自动选择路径，
            # 并写入 StockDataStatus.last_sync_path 供下次决策参考
            summary = await smart_sync(universe=universe, include_intraday=False)
            logger.info("定时数据同步完成 path=%s latest_date=%s",
                        summary.get("path"), summary.get("latest_date_after"))
            return

        except Exception as e:
            err_str = str(e)
            cls = classify_sync_error(err_str)
            logger.error("数据同步失败 (attempt %d/%d) category=%s: %s",
                         attempt, max_retries, cls["category"], err_str[:500])

            # 数据损坏：不重试增量，强制全量重建（下次 smart_sync 会因 latest_date 过期走全量）
            # 此处直接标记状态让下次走全量路径
            if cls["category"] == "data_corrupt":
                logger.warning("数据损坏，标记 latest_date 过期以触发下次全量重建")
                await _invalidate_latest_date(universe)
                if attempt < max_retries:
                    await asyncio.sleep(backoff_seconds[attempt - 1])
                continue

            # 网络错误：指数退避重试
            if cls["category"] == "network" and attempt < max_retries:
                wait = backoff_seconds[attempt - 1]
                logger.info("网络错误，%ds 后重试...", wait)
                await asyncio.sleep(wait)
                continue

            # 其他错误：最后一次或不可重试，记录后退出
            if attempt < max_retries:
                wait = backoff_seconds[attempt - 1]
                logger.info("%ds 后重试...", wait)
                await asyncio.sleep(wait)
            else:
                logger.exception("数据同步最终失败（已重试 %d 次）", max_retries)


async def _invalidate_latest_date(universe: str) -> None:
    """将 latest_date 置空，强制下次 smart_sync 走 chenditc 全量路径。

    用于数据损坏场景的断点续传：不清空已有数据文件，仅让路径判断逻辑
    认为数据需要全量重建（smart_sync.predict_sync_path 在 latest_date=None
    时返回 chenditc_full）。
    """
    from sqlalchemy import select
    try:
        async with async_session() as session:
            rec = await session.execute(
                select(StockDataStatus).where(StockDataStatus.universe == universe)
            )
            row = rec.scalar_one_or_none()
            if row is not None:
                row.latest_date = None
                row.last_error = "数据损坏，已标记需全量重建"
                row.last_updated = datetime.now()
                await session.commit()
    except Exception as e:
        logger.warning("标记 latest_date 失效失败: %s", e)


def register_scheduled_jobs(scheduler):
    """注册定时任务。"""
    # 每工作日 18:00 增量同步 qlib 股票数据
    scheduler.add_job(
        daily_quant_data_update, "cron",
        hour=18, minute=0, day_of_week="mon-fri",
        id="quant_data_update", replace_existing=True,
    )

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
