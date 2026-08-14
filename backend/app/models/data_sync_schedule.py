"""定时数据管理同步设置（单行配置表）。

用户可通过数据管理页配置自动同步：一键全同步（backfill→指数→宏观→财报→外盘）、
增量 EOD、指数、ETF、财报等环节。设计为单行表（id 恒为 1），由调度 tick 每分钟检查，
命中时间窗口即按勾选环节顺序 spawn worker。
"""
from datetime import date, datetime

from sqlalchemy import TIMESTAMP, Boolean, Date, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DataSyncSchedule(Base):
    """定时数据管理同步配置（单行：id=1）。"""

    __tablename__ = "data_sync_schedule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1, comment="固定单行 id=1")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否启用定时同步")
    run_time: Mapped[str] = mapped_column(String(5), default="18:00", comment="每日触发时间 HH:MM")
    workdays_only: Mapped[bool] = mapped_column(Boolean, default=True, comment="仅工作日触发")
    # 环节开关
    include_full: Mapped[bool] = mapped_column(Boolean, default=True, comment="一键全同步（回填→指数→宏观→财报→外盘）")
    include_eod: Mapped[bool] = mapped_column(Boolean, default=False, comment="增量 EOD 同步")
    include_indices: Mapped[bool] = mapped_column(Boolean, default=False, comment="指数同步")
    include_etf: Mapped[bool] = mapped_column(Boolean, default=False, comment="ETF 增量同步")
    include_fundamental: Mapped[bool] = mapped_column(Boolean, default=False, comment="财报同步")
    # 环节参数
    years: Mapped[int] = mapped_column(Integer, default=5, comment="全同步 A股回填年数")
    universe: Mapped[str] = mapped_column(String(32), default="all", comment="股票池（全同步/EOD）")
    eod_days: Mapped[int] = mapped_column(Integer, default=5, comment="EOD 增量同步天数")
    etf_days: Mapped[int] = mapped_column(Integer, default=30, comment="ETF 增量同步天数（自然日）")
    last_run_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="上次成功触发的日期（防重）")
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=True, default=datetime.now, onupdate=datetime.now, comment="更新时间"
    )
