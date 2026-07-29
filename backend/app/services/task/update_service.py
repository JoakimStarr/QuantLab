"""定时任务调度：qlib 股票数据增量同步。"""
import logging
from datetime import datetime
from sqlalchemy import select
from app.core.database import async_session
from app.models.stock_data_status import StockDataStatus

logger = logging.getLogger(__name__)


async def daily_quant_data_update():
    """每日同步 qlib 股票数据（工作日 18:00），含失败重试。

    修复3: 定时同步失败重试（最多 3 次，间隔 10 分钟）
    """
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        logger.info("qlib 不可用，跳过股票数据同步")
        return

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            from app.core.config import settings
            data_source = settings.quant.get("data_source", "chenditc")
            universe = settings.quant.get("universe", "csi300")

            if data_source == "chenditc":
                # 使用 chenditc 全量/增量同步
                from app.services.data.chenditc_client import download_qlib_bin
                from app.services.data.incremental_sync import download_and_merge_incremental
                qlib_dir = settings.qlib_provider_path

                # 先尝试增量同步
                logger.info("尝试增量同步 (attempt %d/%d)", attempt, max_retries)
                result = download_and_merge_incremental(qlib_dir)
                if "error" in result:
                    logger.info("增量同步不可用，使用全量同步")
                    result = download_qlib_bin(qlib_dir)

                logger.info("chenditc 同步完成: %s", result.get("latest_date", "unknown"))
            else:
                # akshare 逐只爬取
                from app.services.quant.data_adapter import sync_to_qlib, get_universe
                universe = settings.quant.get("universe", "csi300")
                async with async_session() as session:
                    rec = await session.execute(
                        select(StockDataStatus).where(StockDataStatus.universe == universe)
                    )
                    row = rec.scalar_one_or_none()
                    start = row.latest_date if row and row.latest_date else "2020-01-01"
                end = datetime.now().strftime("%Y-%m-%d")
                codes = await get_universe()
                logger.info("增量同步 qlib 数据: %s -> %s, %d 只股票", start, end, len(codes))
                await sync_to_qlib(start, end, codes=codes)
                logger.info("qlib 数据增量同步完成")

            # 成功则退出重试循环
            return

        except Exception as e:
            logger.error("数据同步失败 (attempt %d/%d): %s", attempt, max_retries, e)
            if attempt < max_retries:
                import asyncio
                wait_sec = 600  # 10 分钟
                logger.info("等待 %d 秒后重试...", wait_sec)
                await asyncio.sleep(wait_sec)
            else:
                logger.exception("数据同步最终失败（已重试 %d 次）", max_retries)


def register_scheduled_jobs(scheduler):
    """注册定时任务。"""
    # 每工作日 18:00 增量同步 qlib 股票数据
    scheduler.add_job(
        daily_quant_data_update, "cron",
        hour=18, minute=0, day_of_week="mon-fri",
        id="quant_data_update", replace_existing=True,
    )
