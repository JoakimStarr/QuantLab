"""add sync_schedule.last_run_date

Revision ID: r2s3t4u5v6w7
Revises: q1r2s3t4u5v6
Create Date: 2026-08-11

为 sync_schedule 增加 last_run_date 列（当日已触发防重）。

注：新库通过 Base.metadata.create_all 自动建表（模型已含该列），本迁移仅补已有库。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "r2s3t4u5v6w7"
down_revision = "q1r2s3t4u5v6"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "sync_schedule" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("sync_schedule")}
    if "last_run_date" not in columns:
        op.add_column(
            "sync_schedule",
            sa.Column("last_run_date", sa.Date, nullable=True,
                      comment="上次成功触发的日期（防重）"),
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "sync_schedule" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("sync_schedule")}
    if "last_run_date" in columns:
        op.drop_column("sync_schedule", "last_run_date")
