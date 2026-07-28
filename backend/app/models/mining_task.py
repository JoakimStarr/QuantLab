from sqlalchemy import Column, Integer, String, Float, Text, TIMESTAMP, Index
from sqlalchemy.sql import func
from app.core.database import Base


class MiningTask(Base):
    """AI 因子挖掘任务"""
    __tablename__ = "mining_task"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # llm / symbolic / text / automl
    type = Column(String, nullable=False)
    status = Column(String, default="pending")  # pending/running/done/failed
    params = Column(Text, nullable=True)        # JSON: 任务参数
    candidates_generated = Column(Integer, default=0)
    candidates_passed = Column(Integer, default=0)
    best_ic = Column(Float, nullable=True)
    # 生成并通过的因子 id 列表 JSON
    result_factor_ids = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    started_at = Column(TIMESTAMP, nullable=True)
    finished_at = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, default=func.now())

    __table_args__ = (Index("idx_mining_type_status", "type", "status"),)
