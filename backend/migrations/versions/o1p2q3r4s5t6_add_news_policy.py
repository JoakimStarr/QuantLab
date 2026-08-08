"""add news_policy table

Revision ID: o1p2q3r4s5t6
Revises: n7o8p9q0r1s2
Create Date: 2026-08-08

政策风向（央视新闻联播文字稿）——纯文本展示数据，不进宏观数值管线。
按 (news_date, title) 唯一，幂等去重。

注：新建库会通过 Base.metadata.create_all 自动建表，本迁移仅补已有库。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "o1p2q3r4s5t6"
down_revision = "n7o8p9q0r1s2"
branch_labels = None
depends_on = None


def _table_exists(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _table_exists(inspector, "news_policy"):
        return
    op.create_table(
        "news_policy",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("news_date", sa.Date, nullable=False, comment="播出日期"),
        sa.Column("title", sa.String(512), nullable=False, comment="标题"),
        sa.Column("content", sa.Text, nullable=True, comment="全文"),
        sa.Column("source", sa.String(16), nullable=True, comment="数据源"),
        sa.UniqueConstraint("news_date", "title", name="uq_policy_news_date_title"),
    )
    op.create_index("idx_policy_news_date", "news_policy", ["news_date"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _table_exists(inspector, "news_policy"):
        return
    op.drop_index("idx_policy_news_date", table_name="news_policy")
    op.drop_table("news_policy")