"""baostock 全字段数据模型。

覆盖 baostock 提供的全部可落库数据：
- 日K线全字段（stock_daily，含换手/停牌/涨跌幅/ST/估值）
- 证券基本资料（stock_basic）
- 行业分类（stock_industry）
- 交易日历（trade_calendar）
- 季频财务报表（fin_profit/fin_operation/fin_growth/fin_balance/fin_cashflow/fin_dupont）
- 融资融券（margin_daily）
"""
from datetime import date

from sqlalchemy import Boolean, Date, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class StockDaily(Base):
    """日K线全字段（baostock query_daily_history_k_AStock / query_history_k_data_plus）。"""

    __tablename__ = "stock_daily"

    code: Mapped[str] = mapped_column(String(16), primary_key=True, comment="QLib代码 sh600000")
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
    )


class StockBasic(Base):
    """证券基本资料（baostock query_stock_basic）。"""

    __tablename__ = "stock_basic"

    code: Mapped[str] = mapped_column(String(16), primary_key=True, comment="证券代码 sh600000")
    name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="证券名称")
    ipo_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="上市日期")
    out_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="退市日期")
    type: Mapped[str | None] = mapped_column(String(8), nullable=True, comment="证券类型 1股票/2指数/3其它/4可转债/5ETF")
    status: Mapped[str | None] = mapped_column(String(8), nullable=True, comment="上市状态 1上市/0退市")


class StockIndustry(Base):
    """行业分类（baostock query_stock_industry）。"""

    __tablename__ = "stock_industry"

    code: Mapped[str] = mapped_column(String(16), primary_key=True, comment="证券代码")
    code_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="证券名称")
    industry: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="所属行业")
    industry_classification: Mapped[str | None] = mapped_column(String(8), nullable=True, comment="行业分类 sw=申万/zjh=证监会")
    update_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="更新日期")


class TradeCalendar(Base):
    """交易日历（baostock query_trade_dates）。"""

    __tablename__ = "trade_calendar"

    trade_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="日历日期")
    is_trading_day: Mapped[bool | None] = mapped_column(Boolean, nullable=True, comment="是否交易日")


class FinProfit(Base):
    """盈利能力（baostock query_profit_data，季频）。"""

    __tablename__ = "fin_profit"

    code: Mapped[str] = mapped_column(String(16), primary_key=True, comment="证券代码")
    stat_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="统计截止日(报告期)")
    pub_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="发布日期")
    roe_avg: Mapped[float | None] = mapped_column(Float, nullable=True, comment="净资产收益率(平均)")
    np_margin: Mapped[float | None] = mapped_column(Float, nullable=True, comment="销售净利率")
    gp_margin: Mapped[float | None] = mapped_column(Float, nullable=True, comment="销售毛利率")
    net_profit: Mapped[float | None] = mapped_column(Float, nullable=True, comment="净利润")
    eps_ttm: Mapped[float | None] = mapped_column(Float, nullable=True, comment="每股收益(TTM)")
    mb_revenue: Mapped[float | None] = mapped_column(Float, nullable=True, comment="主营营业收入")
    total_share: Mapped[float | None] = mapped_column(Float, nullable=True, comment="总股本")
    liqa_share: Mapped[float | None] = mapped_column(Float, nullable=True, comment="流通股本")

    __table_args__ = (
        Index("idx_fin_profit_code_stat", "code", "stat_date"),
    )


class FinOperation(Base):
    """营运能力（baostock query_operation_data，季频）。"""

    __tablename__ = "fin_operation"

    code: Mapped[str] = mapped_column(String(16), primary_key=True, comment="证券代码")
    stat_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="统计截止日(报告期)")
    pub_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="发布日期")
    nr_turn_ratio: Mapped[float | None] = mapped_column(Float, nullable=True, comment="应收账款周转率")
    nr_turn_days: Mapped[float | None] = mapped_column(Float, nullable=True, comment="应收账款周转天数")
    inv_turn_ratio: Mapped[float | None] = mapped_column(Float, nullable=True, comment="存货周转率")
    inv_turn_days: Mapped[float | None] = mapped_column(Float, nullable=True, comment="存货周转天数")
    ca_turn_ratio: Mapped[float | None] = mapped_column(Float, nullable=True, comment="流动资产周转率")
    ca_turn_days: Mapped[float | None] = mapped_column(Float, nullable=True, comment="流动资产周转天数")
    ar_turn_ratio: Mapped[float | None] = mapped_column(Float, nullable=True, comment="总资产周转率")
    ar_turn_days: Mapped[float | None] = mapped_column(Float, nullable=True, comment="总资产周转天数")

    __table_args__ = (
        Index("idx_fin_operation_code_stat", "code", "stat_date"),
    )


class FinGrowth(Base):
    """成长能力（baostock query_growth_data，季频）。"""

    __tablename__ = "fin_growth"

    code: Mapped[str] = mapped_column(String(16), primary_key=True, comment="证券代码")
    stat_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="统计截止日(报告期)")
    pub_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="发布日期")
    yoy_equity: Mapped[float | None] = mapped_column(Float, nullable=True, comment="净资产同比增长率")
    yoy_asset: Mapped[float | None] = mapped_column(Float, nullable=True, comment="总资产同比增长率")
    yoy_ni: Mapped[float | None] = mapped_column(Float, nullable=True, comment="净利润同比增长率")
    yoy_eps_basic: Mapped[float | None] = mapped_column(Float, nullable=True, comment="基本每股收益同比增长率")
    yoy_pni: Mapped[float | None] = mapped_column(Float, nullable=True, comment="股东权益合计同比增长率")

    __table_args__ = (
        Index("idx_fin_growth_code_stat", "code", "stat_date"),
    )


class FinBalance(Base):
    """偿债能力（baostock query_balance_data，季频）。"""

    __tablename__ = "fin_balance"

    code: Mapped[str] = mapped_column(String(16), primary_key=True, comment="证券代码")
    stat_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="统计截止日(报告期)")
    pub_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="发布日期")
    current_ratio: Mapped[float | None] = mapped_column(Float, nullable=True, comment="流动比率")
    quick_ratio: Mapped[float | None] = mapped_column(Float, nullable=True, comment="速动比率")
    cash_ratio: Mapped[float | None] = mapped_column(Float, nullable=True, comment="现金比率")
    yoy_liability: Mapped[float | None] = mapped_column(Float, nullable=True, comment="总负债同比增长率")
    liability_to_asset: Mapped[float | None] = mapped_column(Float, nullable=True, comment="资产负债率")
    asset_to_equity: Mapped[float | None] = mapped_column(Float, nullable=True, comment="权益乘数")

    __table_args__ = (
        Index("idx_fin_balance_code_stat", "code", "stat_date"),
    )


class FinCashflow(Base):
    """现金流量（baostock query_cash_flow_data，季频）。"""

    __tablename__ = "fin_cashflow"

    code: Mapped[str] = mapped_column(String(16), primary_key=True, comment="证券代码")
    stat_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="统计截止日(报告期)")
    pub_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="发布日期")
    ca_to_asset: Mapped[float | None] = mapped_column(Float, nullable=True, comment="流动资产对总资产的比率")
    nca_to_asset: Mapped[float | None] = mapped_column(Float, nullable=True, comment="非流动资产对总资产的比率")
    tangible_asset_to_asset: Mapped[float | None] = mapped_column(Float, nullable=True, comment="有形资产对总资产的比率")
    ebit_to_interest: Mapped[float | None] = mapped_column(Float, nullable=True, comment="已获利息倍数")
    cfo_to_or: Mapped[float | None] = mapped_column(Float, nullable=True, comment="经营活动产生的现金流量净额对营业收入的比率")
    cfo_to_np: Mapped[float | None] = mapped_column(Float, nullable=True, comment="经营活动产生的现金流量净额对净利润的比率")
    cfo_to_gr: Mapped[float | None] = mapped_column(Float, nullable=True, comment="经营活动产生的现金流量净额对营业总收入(同比增长)")

    __table_args__ = (
        Index("idx_fin_cashflow_code_stat", "code", "stat_date"),
    )


class FinDupont(Base):
    """杜邦分析（baostock query_dupont_data，季频）。"""

    __tablename__ = "fin_dupont"

    code: Mapped[str] = mapped_column(String(16), primary_key=True, comment="证券代码")
    stat_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="统计截止日(报告期)")
    pub_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="发布日期")
    dupont_roe: Mapped[float | None] = mapped_column(Float, nullable=True, comment="净资产收益率")
    dupont_asset_sto_equity: Mapped[float | None] = mapped_column(Float, nullable=True, comment="权益乘数")
    dupont_asset_turn: Mapped[float | None] = mapped_column(Float, nullable=True, comment="总资产周转率")
    dupont_pnitoni: Mapped[float | None] = mapped_column(Float, nullable=True, comment="归属母公司股东的净利润/净利润")
    dupont_nitogr: Mapped[float | None] = mapped_column(Float, nullable=True, comment="净利润/营业总收入")
    dupont_tax_burden: Mapped[float | None] = mapped_column(Float, nullable=True, comment="所得税/利润总额")
    dupont_intburden: Mapped[float | None] = mapped_column(Float, nullable=True, comment="息税前利润/营业总收入")
    dupont_ebittogr: Mapped[float | None] = mapped_column(Float, nullable=True, comment="息税前利润/营业总收入")

    __table_args__ = (
        Index("idx_fin_dupont_code_stat", "code", "stat_date"),
    )


class MarginDaily(Base):
    """融资融券（baostock query_margin_detail，日频）。"""

    __tablename__ = "margin_daily"

    code: Mapped[str] = mapped_column(String(16), primary_key=True, comment="证券代码")
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="交易日期")
    code_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="证券名称")
    rzye: Mapped[float | None] = mapped_column(Float, nullable=True, comment="融资余额")
    rzmre: Mapped[float | None] = mapped_column(Float, nullable=True, comment="融资买入额")
    rqye: Mapped[float | None] = mapped_column(Float, nullable=True, comment="融券余额")
    rqmcl: Mapped[float | None] = mapped_column(Float, nullable=True, comment="融券卖出量")
    rqyl: Mapped[float | None] = mapped_column(Float, nullable=True, comment="融券余量")
    rzrqye: Mapped[float | None] = mapped_column(Float, nullable=True, comment="融资融券余额")
    rzrqyl: Mapped[float | None] = mapped_column(Float, nullable=True, comment="融资融券余量金额")

    __table_args__ = (
        Index("idx_margin_daily_date", "trade_date"),
    )
