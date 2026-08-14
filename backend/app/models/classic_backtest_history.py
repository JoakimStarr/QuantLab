"""经典策略回测历史。

每次运行 /classic-strategies/backtest 自动落库一条记录（配置 + 结果快照），
与 RuleBacktestHistory 并列：前者是单标的模板历史，本表是"学术经典"回测历史。
前端策略库页面将两类历史合并展示、勾选对比、性能聚合。
"""
from sqlalchemy import TIMESTAMP, Column, Float, Index, Integer, String, Text, Boolean
from sqlalchemy.sql import func

from app.core.database import Base


class ClassicBacktestHistory(Base):
    """学术经典策略回测历史（自动保存，配置 + 指标 + 净值/成交快照）"""
    __tablename__ = "classic_backtest_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 策略快照（策略可调整，历史需保留当时的 key/名称/类别）
    strategy_key = Column(String(32), nullable=False)
    strategy_name = Column(String(64), nullable=False)
    category = Column(String(16), nullable=True)
    is_factor = Column(Boolean, nullable=False, default=False)  # True=截面因子型

    # 配置快照
    params = Column(Text, nullable=True, comment="配置参数 JSON（topk/n_drop/标的等）")
    universe = Column(String(16), nullable=True)  # 截面因子型的标的池
    expression = Column(String(255), nullable=True)  # 截面因子型表达式
    benchmark = Column(String(16), nullable=True)
    start_date = Column(String(16), nullable=False)
    end_date = Column(String(16), nullable=False)

    # 核心绩效指标（列表页展示用，避免读大 JSON 字段）
    annual_return = Column(Float, nullable=True)
    annual_volatility = Column(Float, nullable=True)
    sharpe = Column(Float, nullable=True)
    sortino = Column(Float, nullable=True)
    max_drawdown = Column(Float, nullable=True)
    calmar = Column(Float, nullable=True)
    win_rate = Column(Float, nullable=True)
    benchmark_return = Column(Float, nullable=True)
    excess_return = Column(Float, nullable=True)
    n_trades = Column(Integer, nullable=True)

    # 完整结果快照（JSON 字符串）
    metrics = Column(Text, nullable=True)
    nav_curve = Column(Text, nullable=True)
    trades = Column(Text, nullable=True)

    created_at = Column(TIMESTAMP, default=func.now())
    is_deleted = Column(Integer, default=0, nullable=False)  # 软删除标记：0=正常, 1=已删除
    deleted_at = Column(TIMESTAMP, nullable=True)

    __table_args__ = (Index("idx_cbh_created_at", "created_at"),)