"""挖掘任务共享工具函数。"""
import json
from datetime import datetime
from sqlalchemy import select
from app.core.database import async_session
from app.models.mining_task import MiningTask


async def update_task_status(task_id: int, status: str, **kwargs) -> None:
    """更新挖掘任务状态和字段。"""
    async with async_session() as session:
        t = await session.get(MiningTask, task_id)
        if t is None:
            return
        t.status = status
        for key, value in kwargs.items():
            if hasattr(t, key):
                if key in ("params", "result_factor_ids") and isinstance(value, (list, dict)):
                    value = json.dumps(value)
                setattr(t, key, value)
        if status in ("done", "failed"):
            t.finished_at = datetime.now()
        await session.commit()