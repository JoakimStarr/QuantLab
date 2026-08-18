"""挖掘任务共享工具函数。"""
import json
from datetime import datetime
from app.core.database import async_session
from app.models.mining_task import MiningTask


async def update_task_status(task_id: int, status: str = None, **kwargs) -> None:
    """更新挖掘任务状态和字段。

    status 为 None 时仅更新 kwargs 中的字段（用于中途更新 candidates_generated 等指标）。
    """
    async with async_session() as session:
        t = await session.get(MiningTask, task_id)
        if t is None:
            return
        if status is not None:
            t.status = status
        for key, value in kwargs.items():
            if hasattr(t, key):
                if key in ("params", "result_factor_ids", "result") and isinstance(value, (list, dict)):
                    value = json.dumps(value)
                setattr(t, key, value)
        if status in ("done", "failed"):
            t.finished_at = datetime.now()
        await session.commit()
