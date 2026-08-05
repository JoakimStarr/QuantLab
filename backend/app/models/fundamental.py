"""基本面 PIT（Point-in-Time）数据模型。

baostock 返回的 peTTM/pbMRQ/psTTM/pcfNcfTTM 按日频存储，
查询时 WHERE trade_date <= 查询日 ORDER BY trade_date DESC LIMIT 1 保证 PIT 语义。
"""
from datetime import date

from sqlalchemy import Date, Float, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FundamentalPIT(Base):
    """基本面 PIT 数据表（按 code + trade_date 复合主键，日频估值指标）。"""

    __tablename__ = "fundamental_pit"

    code: Mapped[str] = mapped_column(String(16), primary_key=True, comment="QLib代码 sh600000")
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="交易日期")
    pe_ttm: Mapped[float | None] = mapped_column(Float, nullable=True, comment="滚动市盈率")
    pb_mrq: Mapped[float | None] = mapped_column(Float, nullable=True, comment="最近报告期市净率")
    ps_ttm: Mapped[float | None] = mapped_column(Float, nullable=True, comment="滚动市销率")
    pcf_ncf_ttm: Mapped[float | None] = mapped_column(Float, nullable=True, comment="滚动市现率(净现金流)")

    __table_args__ = (
        Index("idx_fund_code_date", "code", "trade_date"),
    )


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
