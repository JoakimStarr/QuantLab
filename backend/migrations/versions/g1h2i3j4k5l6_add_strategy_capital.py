"""add strategy.capital column

Revision ID: g1h2i3j4k5l6
Revises: f6a7b8c9d0e1
Create Date: 2026-08-06

AI 生成策略支持初始资金偏好：策略可按资金规模自动权衡 topk/换手/流动性。
capital 供 AI 参数建议 / AI 复盘感知资金规模（回测仍以回测时的 initial_capital 为准）。

注：新建库会通过 Base.metadata.create_all 自动建列，本迁移仅补已有库。
幂等：用 inspector 检查列是否存在，存在则跳过。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "g1h2i3j4k5l6"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def _column_exists(inspector, table: str, column: str) -> bool:
    if table not in inspector.get_table_names():
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _column_exists(inspector, "strategy", "capital"):
        op.add_column("strategy", sa.Column("capital", sa.Float(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _column_exists(inspector, "strategy", "capital"):
        op.drop_column("strategy", "capital")
