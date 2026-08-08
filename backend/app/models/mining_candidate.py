"""挖掘候选因子记录：任务产生的每个候选（含未通过的），用于复盘。

之前 mining_task 只存统计数（generated/passed），候选内容不落库——
即使 LLM 生成了 50 个候选但 0 个通过，用户也看不到挖过什么、
被哪一关拒绝。本表按 (task_id, expression) 幂等 upsert，
同一表达式的状态随挖掘进度持续更新（沙箱拒绝 → 评价失败 → 通过）。
"""
from sqlalchemy import Column, Integer, String, Float, Text, TIMESTAMP, Index, UniqueConstraint
from datetime import datetime
from app.core.database import Base


class MiningCandidate(Base):
    """AI 因子挖掘候选（含未通过的，用于复盘）"""
    __tablename__ = "mining_candidate"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, nullable=False)
    # 迭代轮次（单轮/挖掘为 1）
    round = Column(Integer, default=1)
    name = Column(String, nullable=True)
    expression = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    # generated（LLM 已返回）/ rejected（沙箱/去重拒绝）/ evaluated（评价未过）/ passed
    status = Column(String, default="generated")
    # 单条拒绝/失败原因（沙箱拒绝、批内去重等）
    reason = Column(Text, nullable=True)
    # 评价失败原因列表 JSON（多维验证 fail_reasons）
    fail_reasons = Column(Text, nullable=True)
    ic = Column(Float, nullable=True)
    rank_ic = Column(Float, nullable=True)
    icir = Column(Float, nullable=True)
    created_at = Column(TIMESTAMP, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("task_id", "expression", name="uq_mining_candidate_task_expr"),
        Index("idx_mining_candidate_task", "task_id"),
    )