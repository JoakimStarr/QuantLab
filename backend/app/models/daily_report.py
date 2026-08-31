"""每日晨报 / 盘前简报（report_date 每天一条）。

聚合各数据源的结构化拼装结果 + LLM 综合研判：
- sections:    结构化拼装结果（policy/external/macro/market 各板块 dict）
- synthesis:   LLM 生成的综合研判 markdown
- focus_sectors: 今日关注板块 [{name, direction, reason}]
- risk_notes:  风险提示 [str, ...]
- outlook:     今日展望（markdown/文本）
- llm_status:  ok（LLM 成功） / degraded（LLM 失败，降级为纯结构化）
"""
from datetime import date, datetime

from sqlalchemy import JSON, TIMESTAMP, Date, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DailyReport(Base):
    """每日晨报（一天一条，report_date 唯一，幂等 upsert）。"""

    __tablename__ = "daily_report"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, comment="主键")
    report_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True, comment="报告日期（每天一条）")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="done", comment="done/failed")
    sections: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="结构化各板块拼装结果")
    synthesis: Mapped[str | None] = mapped_column(Text, nullable=True, comment="LLM 综合研判 markdown")
    focus_sectors: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="今日关注板块")
    risk_notes: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="风险提示")
    outlook: Mapped[str | None] = mapped_column(Text, nullable=True, comment="今日展望")
    llm_status: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="ok/degraded")
    error: Mapped[str | None] = mapped_column(Text, nullable=True, comment="LLM 失败原因")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=True, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=True, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        Index("idx_daily_report_date", "report_date"),
    )
