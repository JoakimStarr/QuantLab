"""add daily_report table

Revision ID: s3t4u5v6w7x8
Revises: r2s3t4u5v6w7
Create Date: 2026-08-21

每日晨报/盘前简报：report_date 每天一条，结构化拼装 + LLM 综合研判（JSON/Text 大字段）。

注：新建库会通过 Base.metadata.create_all 自动建表，本迁移仅补已有库。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "s3t4u5v6w7x8"
down_revision = "r2s3t4u5v6w7"
branch_labels = None
depends_on = None


def _table_exists(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _table_exists(inspector, "daily_report"):
        return
    op.create_table(
        "daily_report",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("report_date", sa.Date, nullable=False, unique=True, comment="报告日期（每天一条）"),
        sa.Column("status", sa.String(16), nullable=False, server_default="done", comment="done/failed"),
        sa.Column("sections", sa.JSON, nullable=True, comment="结构化各板块拼装结果"),
        sa.Column("synthesis", sa.Text, nullable=True, comment="LLM 综合研判 markdown"),
        sa.Column("focus_sectors", sa.JSON, nullable=True, comment="今日关注板块"),
        sa.Column("risk_notes", sa.JSON, nullable=True, comment="风险提示"),
        sa.Column("outlook", sa.Text, nullable=True, comment="今日展望"),
        sa.Column("llm_status", sa.String(16), nullable=True, comment="ok/degraded"),
        sa.Column("error", sa.Text, nullable=True, comment="LLM 失败原因"),
        sa.Column("created_at", sa.TIMESTAMP, nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP, nullable=True),
    )
    op.create_index("idx_daily_report_date", "daily_report", ["report_date"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _table_exists(inspector, "daily_report"):
        return
    op.drop_index("idx_daily_report_date", table_name="daily_report")
    op.drop_table("daily_report")
