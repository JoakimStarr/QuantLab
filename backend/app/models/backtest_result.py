from sqlalchemy import Column, Integer, String, Float, Text, TIMESTAMP, Index, ForeignKey
from sqlalchemy.sql import func
from app.core.database import Base


class BacktestResult(Base):
    """策略回测结果"""
    __tablename__ = "backtest_result"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, ForeignKey("strategy.id"), nullable=False)
    start_date = Column(String, nullable=False)
    end_date = Column(String, nullable=False)

    # 核心绩效指标
    annual_return = Column(Float, nullable=True)
    annual_volatility = Column(Float, nullable=True)
    sharpe = Column(Float, nullable=True)
    sortino = Column(Float, nullable=True)
    max_drawdown = Column(Float, nullable=True)
    calmar = Column(Float, nullable=True)
    turnover = Column(Float, nullable=True)
    win_rate = Column(Float, nullable=True)
    benchmark_return = Column(Float, nullable=True)
    excess_return = Column(Float, nullable=True)

    # 净值曲线与完整指标，JSON 字符串
    nav_curve = Column(Text, nullable=True)
    metrics = Column(Text, nullable=True)

    created_at = Column(TIMESTAMP, default=func.now())

    __table_args__ = (
        Index("idx_backtest_strategy", "strategy_id"),
        Index("idx_backtest_created_at", "created_at"),
        # 参数扫描/历史查重：WHERE strategy_id=? AND start_date=? AND end_date=?
        Index("idx_backtest_strategy_period", "strategy_id", "start_date", "end_date"),
    )
