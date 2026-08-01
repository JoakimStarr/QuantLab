"""数据同步历史记录模型"""
from sqlalchemy import Column, Integer, String, Float, Text, TIMESTAMP, Index
from datetime import datetime
from app.core.database import Base


class SyncHistory(Base):
    """同步历史记录"""
    __tablename__ = "sync_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    universe = Column(String, nullable=False)
    data_source = Column(String, nullable=False)  # chenditc / akshare
    status = Column(String, nullable=False)  # running / ok / failed
    started_at = Column(TIMESTAMP, nullable=False, default=datetime.now)
    finished_at = Column(TIMESTAMP, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    version = Column(String, nullable=True)  # release tag
    release_date = Column(String, nullable=True)
    latest_date = Column(String, nullable=True)  # 数据最新交易日
    stock_count = Column(Integer, nullable=True)
    row_count = Column(Integer, nullable=True)
    file_size_mb = Column(Float, nullable=True)
    error = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_sync_history_universe", "universe"),
        Index("idx_sync_history_status", "status"),
        # 按股票池查最近同步记录：WHERE universe=? ORDER BY started_at DESC LIMIT N
        Index("idx_sync_history_universe_started", "universe", "started_at"),
    )
