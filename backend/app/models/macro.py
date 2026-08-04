"""宏观指标数据模型（东财 datacenter）。

通用窄表设计：任意指标（PMI/CPI/PPI/GDP...）任意字段（MAKE_INDEX/NATIONAL_SAME...）
都存同一张表，以 (indicator, report_date, field_name) 唯一约束，扩展新指标无需改表。

PIT 对齐：available_date = REPORT_DATE + 发布延迟（PMI 0 / CPI PPI 9 / GDP 45），
供 forward-fill 写 qlib bin 时锚定，避免 look-ahead bias。
"""
from datetime import date

from sqlalchemy import Date, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MacroIndicator(Base):
    """宏观指标窄表：indicator + report_date + field_name 唯一。"""

    __tablename__ = "macro_indicator"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="主键")
    indicator: Mapped[str] = mapped_column(String(32), nullable=False, comment="指标代码 PMI/CPI/PPI/GDP")
    report_date: Mapped[date] = mapped_column(Date, nullable=False, comment="报告期(东财 REPORT_DATE)")
    field_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="字段名 MAKE_INDEX/NATIONAL_SAME/...")
    value: Mapped[float | None] = mapped_column(Float, nullable=True, comment="指标值")
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="单位")
    available_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="数据可用日(PIT锚点)")
    source: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="数据源 eastmoney")

    __table_args__ = (
        UniqueConstraint("indicator", "report_date", "field_name", name="uq_macro_indicator"),
        Index("idx_macro_indicator_date", "indicator", "report_date"),
    )
