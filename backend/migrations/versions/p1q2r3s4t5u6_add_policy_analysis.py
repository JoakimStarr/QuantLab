"""add policy_analysis table

Revision ID: p1q2r3s4t5u6
Revises: o1p2q3r4s5t6
Create Date: 2026-08-08

政策风向 AI 解读：每天一条结构化解读（摘要/定调/重磅条目/行业板块/主题热度/关键词）。
JSON 字段存 SQLAlchemy JSON（PG 映射 JSON 类型），news_date 唯一。

注：新建库会通过 Base.metadata.create_all 自动建表，本迁移仅补已有库。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "p1q2r3s4t5u6"
down_revision = "o1p2q3r4s5t6"
branch_labels = None
depends_on = None


def _table_exists(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _table_exists(inspector, "policy_analysis"):
        return
    op.create_table(
        "policy_analysis",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("news_date", sa.Date, nullable=False, comment="播出日期（每天一条）"),
        sa.Column("status", sa.String(16), nullable=False, server_default="done", comment="done/failed"),
        sa.Column("summary", sa.Text, nullable=True, comment="当日政策解读摘要"),
        sa.Column("policy_tone", sa.Text, nullable=True, comment="当日政策定调"),
        sa.Column("key_items", sa.JSON, nullable=True, comment="重磅条目与影响"),
        sa.Column("sectors", sa.JSON, nullable=True, comment="点名行业/板块"),
        sa.Column("topics", sa.JSON, nullable=True, comment="政策主题热度"),
        sa.Column("keywords", sa.JSON, nullable=True, comment="关键词"),
        sa.Column("market_impact", sa.Text, nullable=True, comment="对市场的影响判断"),
        sa.Column("error", sa.Text, nullable=True, comment="失败原因"),
        sa.Column("created_at", sa.TIMESTAMP, nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP, nullable=True),
        sa.UniqueConstraint("news_date", name="uq_policy_analysis_date"),
    )
    op.create_index("idx_policy_analysis_date", "policy_analysis", ["news_date"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _table_exists(inspector, "policy_analysis"):
        return
    op.drop_index("idx_policy_analysis_date", table_name="policy_analysis")
    op.drop_table("policy_analysis")