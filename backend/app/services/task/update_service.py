"""定时任务调度：qlib 股票数据增量同步。"""
import logging
from datetime import datetime
from sqlalchemy import select
from app.core.database import async_session
from app.models.stock_data_status import StockDataStatus

logger = logging.getLogger(__name__)


async def daily_quant_data_update():
    """每日增量同步 qlib 股票数据（工作日 18:00）。"""
    from app.services.quant.qlib_init import is_qlib_available
    if not is_qlib_available():
        logger.info("qlib 不可用，跳过股票数据同步")
        return
    try:
        from app.services.quant.data_adapter import sync_to_qlib, get_universe
        # 读取上次同步日期作为增量起点
        async with async_session() as session:
            rec = await session.execute(
                select(StockDataStatus).where(StockDataStatus.universe == "csi300")
            )
            row = rec.scalar_one_or_none()
            start = row.latest_date if row and row.latest_date else "2020-01-01"
        end = datetime.now().strftime("%Y-%m-%d")
        codes = await get_universe()
        logger.info("增量同步 qlib 数据: %s -> %s, %d 只股票", start, end, len(codes))
        await sync_to_qlib(start, end, codes=codes)
        logger.info("qlib 数据增量同步完成")
    except Exception as e:
        logger.exception("qlib 数据同步失败: %s", e)


def register_scheduled_jobs(scheduler):
    """注册定时任务。"""
    # 每工作日 18:00 增量同步 qlib 股票数据
    scheduler.add_job(
        daily_quant_data_update, "cron",
        hour=18, minute=0, day_of_week="mon-fri",
        id="quant_data_update", replace_existing=True,
    )
