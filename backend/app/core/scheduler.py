from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")


async def start_scheduler():
    from app.services.task.update_service import register_scheduled_jobs
    register_scheduled_jobs(scheduler)
    scheduler.start()


async def stop_scheduler():
    scheduler.shutdown(wait=True)