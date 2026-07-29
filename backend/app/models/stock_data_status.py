from datetime import datetime
from sqlalchemy import Column, Integer, String, TIMESTAMP, UniqueConstraint
from sqlalchemy.sql import func
from app.core.database import Base


class StockDataStatus(Base):
    """股票量化数据同步状态（记录 qlib bin 数据的新鲜度）"""
    __tablename__ = "stock_data_status"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 数据范围标识：universe 名称（csi300/csi500/all）或具体代码
    universe = Column(String, nullable=False)
    latest_date = Column(String, nullable=True)
    row_count = Column(Integer, default=0)
    stock_count = Column(Integer, default=0)
    last_updated = Column(TIMESTAMP, nullable=True, default=datetime.now)
    status = Column(String, default="ok")  # ok / syncing / failed / empty
    last_error = Column(String, nullable=True)
    qlib_dir = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("universe", name="uq_stock_data_status_universe"),
    )
