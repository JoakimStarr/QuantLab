"""基本面数据模型。

- financial_indicator：季频财报窄表（akshare 财务摘要，通用字段 + PIT 发布日期）。
  与 macro_indicator 同构：任意股票 × 报告期 × 字段，扩展新指标只需加配置。
  available_date = 报告期 + 法定披露截止延迟（近似 pub_date，防 look-ahead）。
"""
from datetime import date

from sqlalchemy import Date, Float, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FinancialIndicator(Base):
    """季频财报窄表（akshare 财务摘要，通用字段 + PIT 发布日期）。

    与 macro_indicator 同构：任意股票 × 报告期 × 字段，扩展新指标只需加配置。
    available_date = 报告期 + 法定披露截止延迟（近似 pub_date，防 look-ahead）。
    """

    __tablename__ = "financial_indicator"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), comment="QLib代码 sh600000")
    report_date: Mapped[date] = mapped_column(Date, comment="报告期（季度截止日）")
    field_name: Mapped[str] = mapped_column(String(64), comment="字段名（bin 广播同名）")
    value: Mapped[float | None] = mapped_column(Float, nullable=True, comment="数值")
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="单位（元/%/倍/天）")
    available_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="PIT 可用日（报告期+披露延迟）")
    source: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="数据源")

    __table_args__ = (
        UniqueConstraint("code", "report_date", "field_name", name="uq_financial_indicator"),
        Index("idx_fin_indicator_code_date", "code", "available_date"),
    )
