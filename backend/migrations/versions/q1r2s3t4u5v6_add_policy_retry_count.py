"""add policy_analysis.retry_count

Revision ID: q1r2s3t4u5v6
Revises: p1q2r3s4t5u6
Create Date: 2026-08-08

为 policy_analysis 增加 retry_count 列（AI 解读失败重试次数，用于重试上限/冷却）。

注：新库通过 Base.metadata.create_all 自动建表（模型已含该列），本迁移仅补已有库。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "q1r2s3t4u5v6"
down_revision = "p1q2r3s4t5u6"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "policy_analysis" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("policy_analysis")}
    if "retry_count" not in columns:
        op.add_column(
            "policy_analysis",
            sa.Column("retry_count", sa.Integer, nullable=False, server_default="0",
                      comment="AI 解读失败重试次数"),
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "policy_analysis" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("policy_analysis")}
    if "retry_count" in columns:
        op.drop_column("policy_analysis", "retry_count")
