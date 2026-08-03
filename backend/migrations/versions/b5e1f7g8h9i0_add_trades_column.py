"""add trades column to backtest_result

Revision ID: b5e6f7g8h9i0
Revises: d4e5f6g7h8i9
Create Date: 2026-08-02
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "b5e6f7g8h9i0"
down_revision = "d4e5f6g7h8i9"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    """用 inspector 检查列是否存在（与其它迁移一致，兼容 create_all 已建列的新库）。"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    if not _column_exists("backtest_result", "trades"):
        op.add_column(
            "backtest_result",
            sa.Column("trades", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    if _column_exists("backtest_result", "trades"):
        op.drop_column("backtest_result", "trades")
