"""因子评价/补算后台任务（独立 job 表 + worker 子进程）。

与 mining_task 同源设计：长计算（qlib 因子 IC 评价可到分钟级）不占 web
事件循环、uvicorn --reload 不杀任务、可查询进度/取消，并允许用户离开页面
稍后回来收结果。通用结构可复用给参数扫描/回测重算等其它长任务。
"""
from sqlalchemy import TIMESTAMP, Column, Index, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class FactorEvalJob(Base):
    """因子评价 job：single（单因子）或 batch（批量补算）。"""
    __tablename__ = "factor_eval_job"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # single / batch
    kind = Column(String(16), nullable=False, default="batch")
    # 参与评价的因子 id 列表 JSON
    factor_ids = Column(Text, nullable=False)
    start_date = Column(String, nullable=True)
    end_date = Column(String, nullable=True)
    universe = Column(String, nullable=True)

    # pending / running / done / failed / cancelled
    status = Column(String(16), nullable=False, default="pending")
    total = Column(Integer, nullable=False, default=0)
    done = Column(Integer, nullable=False, default=0)
    # 正在处理的因子（展示用）
    current_label = Column(String, nullable=True)
    error = Column(Text, nullable=True)
    # 逐因子结果摘要 JSON：{factor_id: {ic/rank_ic/... 或 error}}
    result = Column(Text, nullable=True)

    started_at = Column(TIMESTAMP, nullable=True)
    finished_at = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, default=func.now())
    is_deleted = Column(Integer, default=0, nullable=False)

    __table_args__ = (
        Index("idx_eval_job_status_created", "status", "created_at"),
    )
