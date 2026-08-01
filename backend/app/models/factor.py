from sqlalchemy import Column, Integer, String, Float, Text, TIMESTAMP, Index
from sqlalchemy.sql import func
from app.core.database import Base


class Factor(Base):
    """量化因子库"""
    __tablename__ = "factor"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    # qlib 因子表达式，如 "Ref($close,-20)/$close - 1"
    expression = Column(Text, nullable=False)
    # builtin / llm / symbolic / text
    category = Column(String, nullable=False, default="builtin")
    description = Column(Text, nullable=True)

    # 因子评价指标
    ic = Column(Float, nullable=True)
    rank_ic = Column(Float, nullable=True)
    icir = Column(Float, nullable=True)
    ir = Column(Float, nullable=True)            # 信息比率
    turnover = Column(Float, nullable=True)
    decay = Column(Text, nullable=True)          # IC 衰减曲线 JSON {lag: ic}

    # 评价区间
    eval_start = Column(String, nullable=True)
    eval_end = Column(String, nullable=True)
    evaluated_at = Column(TIMESTAMP, nullable=True)

    status = Column(String, default="active")    # active / disabled
    source_task_id = Column(Integer, nullable=True)
    created_at = Column(TIMESTAMP, default=func.now())

    __table_args__ = (
        Index("idx_factor_category_status", "category", "status"),
        Index("idx_factor_ic", "ic"),
        Index("idx_factor_name", "name"),
        # 按挖掘任务反查因子：WHERE source_task_id=?（挖掘结果列表高频查询）
        Index("idx_factor_source_task", "source_task_id"),
    )
