"""指数/ETF 主数据表。

记录 qlib bin 中存在的指数（sh000001/sz399001...）与 ETF（sh510300...），
用于数据校验/补齐时区分"股票"与"指数/ETF"两类 instrument：

- 指数来自 index_sync.py，ETF 来自 etf_sync.py，都只写 OHLCV 字段，
  不要求 18 个股票 BIN_FIELDS，也没有 stock_daily / 财报数据。
- 校验（check_fields/check_macro/check_coverage）与补齐（repair）通过
  本表判断某目录是否为指数/ETF，从而跳过对股票的专属要求。
"""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class StockIndex(Base):
    """指数/ETF 主表：code 唯一（入库统一大写，如 SH000001）。

    type: 'index'=指数（默认）/ 'etf'=ETF。两种标的都只写 OHLCV bin，
    validation/repair 通过 ``load_index_codes()`` 一并排除（返回全部 code）。
    """

    __tablename__ = "stock_index"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="主键")
    code: Mapped[str] = mapped_column(String(16), nullable=False, comment="代码（大写,如 SH000001）")
    name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="名称(指数或ETF)")
    source: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="数据源 baostock/akshare")
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="index", comment="标的类型 index/etf")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    __table_args__ = (
        Index("uq_stock_index_code", "code", unique=True),
        # DB 统一大写口径（与库内 ck 约束同名，create_all 新库自动带上）
        CheckConstraint("code = UPPER(code)", name="ck_stock_index_code_uppercase"),
    )
