"""政策风向数据模型（央视新闻联播文字稿 + AI 解读）。

文本型数据：只存储展示（政策方向、题材追踪），不接宏观数值管线、不写 qlib bin。
news_policy 以 (news_date, title) 唯一，同日同标题幂等去重；
policy_analysis 每天一条（news_date 唯一），AI 生成的结构化解读（JSON 字段）。
"""
from datetime import date, datetime

from sqlalchemy import JSON, Date, Index, String, Text, TIMESTAMP, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PolicyNews(Base):
    """新闻联播文字稿条目。"""

    __tablename__ = "news_policy"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, comment="主键")
    news_date: Mapped[date] = mapped_column(Date, nullable=False, comment="播出日期")
    title: Mapped[str] = mapped_column(String(512), nullable=False, comment="标题")
    content: Mapped[str | None] = mapped_column(Text, nullable=True, comment="全文")
    source: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="数据源")

    __table_args__ = (
        UniqueConstraint("news_date", "title", name="uq_policy_news_date_title"),
        Index("idx_policy_news_date", "news_date"),
    )


class PolicyAnalysis(Base):
    """每日新闻联播的 AI 结构化解读（一天一条）。

    key_items: [{title, impact}]  重磅条目 + 影响
    sectors:   [{name, direction(利好/利空/中性), reason}] 点名行业/板块
    topics:    [{topic, score(0~1)}] 政策主题与热度
    keywords:  [str, ...] 关键词（供内容检索）
    """

    __tablename__ = "policy_analysis"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, comment="主键")
    news_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True, comment="播出日期（每天一条）")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="done", comment="done/failed")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True, comment="当日政策解读摘要")
    policy_tone: Mapped[str | None] = mapped_column(Text, nullable=True, comment="当日政策定调")
    key_items: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="重磅条目与影响")
    sectors: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="点名行业/板块")
    topics: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="政策主题热度")
    keywords: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="关键词")
    market_impact: Mapped[str | None] = mapped_column(Text, nullable=True, comment="对市场的影响判断")
    error: Mapped[str | None] = mapped_column(Text, nullable=True, comment="失败原因")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=True, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=True, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        Index("idx_policy_analysis_date", "news_date"),
    )