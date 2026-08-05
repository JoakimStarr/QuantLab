"""指数主数据表。

记录 qlib bin 中存在的指数（sh000001/sz399001...），用于数据校验/补齐时
区分"股票"与"指数"两类 instrument：

- 指数来自 index_sync.py（akshare/baostock 指数日K），只写 OHLCV 字段，
  不要求 18 个股票 BIN_FIELDS，也没有 stock_daily / 财报数据。
- 校验（check_fields/check_macro/check_coverage）与补齐（repair）通过
  本表判断某目录是否为指数，从而跳过对指数的股票专属要求。
"""
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class StockIndex(Base):
    """指数主表：code（qlib 代码，小写）唯一。"""

    __tablename__ = "stock_index"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="主键")
    code: Mapped[str] = mapped_column(String(16), nullable=False, comment="qlib 指数代码(小写,如 sh000001)")
    name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="指数名称(如 上证指数)")
    source: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="数据源 baostock/akshare")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    __table_args__ = (
        Index("uq_stock_index_code", "code", unique=True),
    )
