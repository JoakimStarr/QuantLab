"""add trades column to backtest_result

Revision ID: b5e6f7g8h9i0
Revises: d4e5f6g7h8i9
Create Date: 2026-08-02
"""
from alembic import op

# revision identifiers
revision = "b5e6f7g8h9i0"
down_revision = "d4e5f6g7h8i9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    exists = False
    try:
        if dialect == "postgresql":
            res = bind.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='backtest_result' AND column_name='trades'"
            )
            exists = res.fetchone() is not None
        else:
            res = bind.execute("PRAGMA table_info(backtest_result)")
            exists = any(row[1] == "trades" for row in res.fetchall())
    except Exception:
        exists = False
    if not exists:
        op.execute("ALTER TABLE backtest_result ADD COLUMN trades TEXT")


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect
    try:
        if dialect == "postgresql":
            op.execute("ALTER TABLE backtest_result DROP COLUMN trades")
        else:
            op.execute("ALTER TABLE backtest_result DROP COLUMN trades")
    except Exception:
        pass