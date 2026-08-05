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

    # ===== 回测参数快照（v2.4.1 起持久化，保证每条结果自带完整配置） =====
    # 策略默认回测时从 strategy 取值写入；参数扫描时为各候选组合
    topk = Column(Integer, nullable=True)
    n_drop = Column(Integer, nullable=True)
    rebalance_freq = Column(String, nullable=True)
    # 组合方式（equal_weight/ic_weight/ir_weight）、正交化开关、基准、回测引擎
    combination_method = Column(String, nullable=True)
    orthogonalize = Column(Integer, nullable=True)
    benchmark = Column(String, nullable=True)
    backend = Column(String, nullable=True)
    # 初始资金（元）
    initial_capital = Column(Float, nullable=True)

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

    # 逐笔成交明细（回测动作：BUY/SELL），JSON 字符串
    trades = Column(Text, nullable=True)

    created_at = Column(TIMESTAMP, default=func.now())
    is_deleted = Column(Integer, default=0, nullable=False)  # 软删除标记：0=正常, 1=已删除
    deleted_at = Column(TIMESTAMP, nullable=True)

    __table_args__ = (
        Index("idx_backtest_strategy", "strategy_id"),
        Index("idx_backtest_created_at", "created_at"),
        # 参数扫描/历史查重：WHERE strategy_id=? AND start_date=? AND end_date=?
        Index("idx_backtest_strategy_period", "strategy_id", "start_date", "end_date"),
        # 参数扫描持久化缓存精确查重：
        # WHERE strategy_id=? AND start_date=? AND end_date=? AND topk=? AND rebalance_freq=?
        Index("idx_backtest_sweep_lookup",
              "strategy_id", "start_date", "end_date", "topk", "rebalance_freq"),
    )
