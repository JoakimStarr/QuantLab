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
    # AI 因子解释：结构化 JSON（{summary, logic, rationale, caveats, generated_at}），
    # description 字段只存 summary 一句话简述
    ai_explanation = Column(Text, nullable=True)
    # AI 追问对话历史：JSON 数组 [{role, content, ts}]，持久化保存
    ai_chat_history = Column(Text, nullable=True)

    # 因子评价指标
    ic = Column(Float, nullable=True)
    rank_ic = Column(Float, nullable=True)
    icir = Column(Float, nullable=True)
    ir = Column(Float, nullable=True)            # 信息比率
    turnover = Column(Float, nullable=True)
    decay = Column(Text, nullable=True)          # IC 衰减曲线 JSON {lag: ic}
    # 多周期评价：{horizon: ic} JSON
    ic_by_horizon = Column(Text, nullable=True)
    # 正交后残差 IC（对已有基准因子正交后的增量 alpha）
    orthogonal_ic = Column(Float, nullable=True)

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
