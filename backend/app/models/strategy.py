from sqlalchemy import Column, Integer, String, Float, Text, TIMESTAMP, Index
from sqlalchemy.sql import func
from app.core.database import Base


class Strategy(Base):
    """量化策略"""
    __tablename__ = "strategy"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    # 关联因子 id 列表，JSON 字符串 [1,2,3]
    factor_ids = Column(Text, nullable=False, default="[]")
    # 因子组合方式：equal_weight / ic_weight / lightgbm / stacking
    combination_method = Column(String, default="equal_weight")
    # 选股参数
    topk = Column(Integer, default=50)
    n_drop = Column(Integer, default=5)
    rebalance_freq = Column(String, default="day")  # day / week / month
    benchmark = Column(String, default="SH000300")
    # 是否启用因子 Gram-Schmidt 正交化（0/1）
    orthogonalize = Column(Integer, default=0)

    status = Column(String, default="active")  # active / archived
    created_at = Column(TIMESTAMP, default=func.now())
    updated_at = Column(TIMESTAMP, default=func.now(), onupdate=func.now())

    __table_args__ = (Index("idx_strategy_status", "status"),)
