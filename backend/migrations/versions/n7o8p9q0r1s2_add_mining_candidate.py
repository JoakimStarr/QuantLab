"""add mining_candidate table

Revision ID: n7o8p9q0r1s2
Revises: m7n8o9p0q1r2
Create Date: 2026-08-08

挖掘候选因子记录（含未通过的，供复盘「挖过什么、被哪一关拒绝」）。
按 (task_id, expression) 唯一，挖掘过程中幂等 upsert 更新状态。

注：新建库会通过 Base.metadata.create_all 自动建表，本迁移仅补已有库。
幂等：用 inspector 检查表是否存在，存在则跳过。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "n7o8p9q0r1s2"
down_revision = "m7n8o9p0q1r2"
branch_labels = None
depends_on = None


def _table_exists(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _table_exists(inspector, "mining_candidate"):
        return
    op.create_table(
        "mining_candidate",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer, nullable=False, comment="mining_task.id"),
        sa.Column("round", sa.Integer, nullable=False, server_default="1", comment="迭代轮次"),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("expression", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="generated"),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("fail_reasons", sa.Text, nullable=True),
        sa.Column("ic", sa.Float, nullable=True),
        sa.Column("rank_ic", sa.Float, nullable=True),
        sa.Column("icir", sa.Float, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP, nullable=True),
        sa.UniqueConstraint("task_id", "expression", name="uq_mining_candidate_task_expr"),
    )
    op.create_index("idx_mining_candidate_task", "mining_candidate", ["task_id"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _table_exists(inspector, "mining_candidate"):
        op.drop_index("idx_mining_candidate_task", table_name="mining_candidate")
        op.drop_table("mining_candidate")