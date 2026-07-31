from sqlalchemy import Column, Integer, String, Float, TIMESTAMP, Index
from sqlalchemy.sql import func
from app.core.database import Base


class FundamentalPIT(Base):
    """基本面PIT数据（按公告日过滤，避免未来函数）

    每条记录是一个报告期的快照。同一报告期被追溯调整时，按 announce_date
    保留多版本。查询时用 WHERE announce_date <= 交易日 ORDER BY announce_date DESC
    取最近版本。
    """
    __tablename__ = "fundamental_pit"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # qlib代码格式 SH600000
    code = Column(String, nullable=False)
    # 报告期 2023-12-31
    report_date = Column(String, nullable=False)
    # 公告日（PIT关键字段，查询时按此过滤）
    announce_date = Column(String, nullable=False)

    # 利润表
    revenue = Column(Float, nullable=True)          # 营业收入（元）
    net_profit = Column(Float, nullable=True)       # 归母净利润（元）
    net_profit_excl = Column(Float, nullable=True)  # 扣非净利润（元）

    # 资产负债表
    total_assets = Column(Float, nullable=True)     # 总资产（元）
    net_assets = Column(Float, nullable=True)       # 净资产（归母股东权益，元）

    # 每股指标
    eps = Column(Float, nullable=True)              # 每股收益
    bps = Column(Float, nullable=True)              # 每股净资产
    roe = Column(Float, nullable=True)              # 净资产收益率（%）

    # 估值指标（日频快照，非报告期数据，report_date=当日）
    pe = Column(Float, nullable=True)               # 市盈率（TTM）
    pb = Column(Float, nullable=True)               # 市净率
    ps = Column(Float, nullable=True)               # 市销率
    total_mv = Column(Float, nullable=True)         # 总市值（元）

    # 元数据
    source = Column(String, default="akshare")      # 数据源
    fetched_at = Column(TIMESTAMP, default=func.now())

    __table_args__ = (
        Index("idx_fund_code_date", "code", "announce_date"),
        Index("idx_fund_report", "code", "report_date"),
    )
