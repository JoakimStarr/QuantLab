"""baostock 全字段数据模型。

覆盖 baostock 提供的全部可落库数据：
- 日K线全字段（stock_daily，含换手/停牌/涨跌幅/ST/估值）
- ETF 日K窄表（etf_daily，仅 OHLCV/量/额/涨跌幅）
- 证券基本资料（stock_basic）
- 行业分类（stock_industry）
- 交易日历（trade_calendar）
"""
from datetime import date

from sqlalchemy import Boolean, CheckConstraint, Date, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class StockDaily(Base):
    """日K线全字段（baostock query_daily_history_k_AStock / query_history_k_data_plus）。"""

    __tablename__ = "stock_daily"

    code: Mapped[str] = mapped_column(String(16), primary_key=True, comment="QLib代码（大写 SH600000）")
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="交易日期")
    open: Mapped[float | None] = mapped_column(Float, nullable=True, comment="开盘价")
    high: Mapped[float | None] = mapped_column(Float, nullable=True, comment="最高价")
    low: Mapped[float | None] = mapped_column(Float, nullable=True, comment="最低价")
    close: Mapped[float | None] = mapped_column(Float, nullable=True, comment="收盘价")
    preclose: Mapped[float | None] = mapped_column(Float, nullable=True, comment="昨收价")
    volume: Mapped[float | None] = mapped_column(Float, nullable=True, comment="成交量(股)")
    amount: Mapped[float | None] = mapped_column(Float, nullable=True, comment="成交额(元)")
    adjustflag: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="复权状态 1后复权/2前复权/3不复权")
    turn: Mapped[float | None] = mapped_column(Float, nullable=True, comment="换手率(%)")
    tradestatus: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="交易状态 1正常/0停牌")
    pct_chg: Mapped[float | None] = mapped_column(Float, nullable=True, comment="涨跌幅(%)")
    is_st: Mapped[bool | None] = mapped_column(Boolean, nullable=True, comment="是否ST")
    pe_ttm: Mapped[float | None] = mapped_column(Float, nullable=True, comment="滚动市盈率")
    pb_mrq: Mapped[float | None] = mapped_column(Float, nullable=True, comment="最近报告期市净率")
    ps_ttm: Mapped[float | None] = mapped_column(Float, nullable=True, comment="滚动市销率")
    pcf_ncf_ttm: Mapped[float | None] = mapped_column(Float, nullable=True, comment="滚动市现率(净现金流)")

    __table_args__ = (
        # 日频查询：按日期拉全市场
        Index("idx_stock_daily_date", "trade_date"),
        # DB 统一大写口径（与库内 ck 约束同名，create_all 新库自动带上）
        CheckConstraint("code = UPPER(code)", name="ck_stock_daily_code_uppercase"),
    )


class StockBasic(Base):
    """证券基本资料（baostock query_stock_basic）。"""

    __tablename__ = "stock_basic"

    code: Mapped[str] = mapped_column(String(16), primary_key=True, comment="证券代码（大写 SH600000）")
    name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="证券名称")
    ipo_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="上市日期")
    out_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="退市日期")
    type: Mapped[str | None] = mapped_column(String(8), nullable=True, comment="证券类型 1股票/2指数/3其它/4可转债/5ETF")
    status: Mapped[str | None] = mapped_column(String(8), nullable=True, comment="上市状态 1上市/0退市")

    __table_args__ = (
        # DB 统一大写口径（与库内 ck 约束同名，create_all 新库自动带上）
        CheckConstraint("code = UPPER(code)", name="ck_stock_basic_code_uppercase"),
    )


class EtfDaily(Base):
    """ETF 日K线窄表（baostock query_daily_history_k_ETF）。

    只存精选池筛选 / 未来 repair 重建所需的必要字段；不混入 stock_daily
    （ETF 无股票 BIN_FIELDS/财报，混入会污染 fieldset 与日历一致性检查）。
    """

    __tablename__ = "etf_daily"

    code: Mapped[str] = mapped_column(String(16), primary_key=True, comment="QLib代码（大写 SH510300）")
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="交易日期")
    open: Mapped[float | None] = mapped_column(Float, nullable=True, comment="开盘价")
    high: Mapped[float | None] = mapped_column(Float, nullable=True, comment="最高价")
    low: Mapped[float | None] = mapped_column(Float, nullable=True, comment="最低价")
    close: Mapped[float | None] = mapped_column(Float, nullable=True, comment="收盘价")
    volume: Mapped[float | None] = mapped_column(Float, nullable=True, comment="成交量(份)")
    amount: Mapped[float | None] = mapped_column(Float, nullable=True, comment="成交额(元)")
    pct_chg: Mapped[float | None] = mapped_column(Float, nullable=True, comment="涨跌幅(%)")

    __table_args__ = (
        # 按日期查询全市场（精选池筛选窗口）
        Index("idx_etf_daily_date", "trade_date"),
        # DB 统一大写口径（与库内 ck 约束同名，create_all 新库自动带上）
        CheckConstraint("code = UPPER(code)", name="ck_etf_daily_code_uppercase"),
    )


class StockIndustry(Base):
    """行业分类（baostock query_stock_industry）。"""

    __tablename__ = "stock_industry"

    code: Mapped[str] = mapped_column(String(16), primary_key=True, comment="证券代码（大写 SH600000）")
    code_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="证券名称")
    industry: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="所属行业")
    industry_classification: Mapped[str | None] = mapped_column(String(8), nullable=True, comment="行业分类 sw=申万/zjh=证监会")
    update_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="更新日期")

    __table_args__ = (
        # DB 统一大写口径（与库内 ck 约束同名，create_all 新库自动带上）
        CheckConstraint("code = UPPER(code)", name="ck_stock_industry_code_uppercase"),
    )


class TradeCalendar(Base):
    """交易日历（baostock query_trade_dates）。"""

    __tablename__ = "trade_calendar"

    trade_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="日历日期")
    is_trading_day: Mapped[bool | None] = mapped_column(Boolean, nullable=True, comment="是否交易日")
