"""定时数据刷新设置（单行配置表）。

用户可通过前端配置自动同步：新闻联播抓取、AI 政策解读、行情(EOD)增量同步。
设计为单行表（id 恒为 1），由调度 tick 每分钟检查，命中时间窗口即触发 worker。
"""
from datetime import date, datetime

from sqlalchemy import TIMESTAMP, Boolean, Date, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SyncSchedule(Base):
    """定时数据刷新配置（单行：id=1）。"""

    __tablename__ = "sync_schedule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1, comment="固定单行 id=1")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否启用定时刷新")
    run_time: Mapped[str] = mapped_column(String(5), default="18:00", comment="每日触发时间 HH:MM")
    workdays_only: Mapped[bool] = mapped_column(Boolean, default=True, comment="仅工作日触发")
    # 各环节开关
    include_news: Mapped[bool] = mapped_column(Boolean, default=True, comment="同步新闻联播")
    include_ai: Mapped[bool] = mapped_column(Boolean, default=True, comment="生成 AI 政策解读")
    include_market: Mapped[bool] = mapped_column(Boolean, default=True, comment="行情 EOD 增量同步")
    # 环节参数
    ai_backfill_days: Mapped[int] = mapped_column(Integer, default=30, comment="AI 解读回填窗口（天）")
    market_days: Mapped[int] = mapped_column(Integer, default=5, comment="EOD 增量同步天数")
    market_universe: Mapped[str] = mapped_column(String(32), default="csi300", comment="EOD 股票池")
    last_run_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="上次成功触发的日期（防重）")
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=True, default=datetime.now, onupdate=datetime.now, comment="更新时间"
    )
