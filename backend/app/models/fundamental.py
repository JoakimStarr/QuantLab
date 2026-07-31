"""基本面 PIT（Point-in-Time）数据模型。

baostock 返回的 peTTM/pbMRQ/psTTM/pcfNcfTTM 按日频存储，
查询时 WHERE trade_date <= 查询日 ORDER BY trade_date DESC LIMIT 1 保证 PIT 语义。
"""
from datetime import date

from sqlalchemy import Date, Float, Index, String
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
