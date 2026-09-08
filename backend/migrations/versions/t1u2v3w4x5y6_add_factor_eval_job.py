"""add factor_eval_job table

Revision ID: t1u2v3w4x5y6
Revises: s3t4u5v6w7x8
Create Date: 2026-09-08

因子评价/补算后台任务表（single/batch → 独立 worker 子进程执行）。

注：新建库会通过 Base.metadata.create_all 自动建表，本迁移仅补已有库。
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "t1u2v3w4x5y6"
down_revision = "s3t4u5v6w7x8"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "factor_eval_job" in inspector.get_table_names():
        return
    op.create_table(
        "factor_eval_job",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(16), nullable=False, server_default="batch", comment="single/batch"),
        sa.Column("factor_ids", sa.Text, nullable=False, comment="因子 id 列表 JSON"),
        sa.Column("start_date", sa.String, nullable=True),
        sa.Column("end_date", sa.String, nullable=True),
        sa.Column("universe", sa.String, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending",
                  comment="pending/running/done/failed/cancelled"),
        sa.Column("total", sa.Integer, nullable=False, server_default="0"),
        sa.Column("done", sa.Integer, nullable=False, server_default="0"),
        sa.Column("current_label", sa.String, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("result", sa.Text, nullable=True, comment="逐因子结果摘要 JSON"),
        sa.Column("started_at", sa.TIMESTAMP, nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP, nullable=True),
        sa.Column("is_deleted", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("idx_eval_job_status_created", "factor_eval_job", ["status", "created_at"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "factor_eval_job" not in inspector.get_table_names():
        return
    op.drop_index("idx_eval_job_status_created", table_name="factor_eval_job")
    op.drop_table("factor_eval_job")
