from sqlalchemy import Column, Integer, String, Text, TIMESTAMP, Index
from sqlalchemy.sql import func
from app.core.database import Base


class TaskResult(Base):
    """异步任务结果（参数扫描 / walk-forward 等），替代 settings 单例内存存储。

    持久化后可跨重启/多 worker 读取，避免内存泄漏与状态丢失。
    """
    __tablename__ = "task_result"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, nullable=True)
    task_type = Column(String, nullable=False)  # param-sweep / walk-forward
    status = Column(String, nullable=False, default="running")  # running/done/failed
    payload = Column(Text, nullable=True)  # JSON 结果
    error = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP, default=func.now())
    updated_at = Column(TIMESTAMP, default=func.now(), onupdate=func.now())

    __table_args__ = (Index("idx_task_result_strategy_type", "strategy_id", "task_type"),)
