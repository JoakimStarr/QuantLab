"""drop legacy fundamental_pit table

Revision ID: j4k5l6m7n8o9
Revises: i3j4k5l6m7n8
Create Date: 2026-08-06

fundamental_pit 是旧"日频估值 PIT"方案的遗留表（0 行、只写不读）：
- 日频估值已由 stock_daily（pe_ttm/pb_mrq/ps_ttm/pcf_ncf_ttm 列）覆盖
- 季频财报 PIT 由 financial_indicator 窄表覆盖
删除该表收敛 schema。

幂等：用 inspector 检查表是否存在，存在则删。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "j4k5l6m7n8o9"
down_revision = "i3j4k5l6m7n8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "fundamental_pit" in inspector.get_table_names():
        op.drop_table("fundamental_pit")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "fundamental_pit" not in inspector.get_table_names():
        op.create_table(
            "fundamental_pit",
            sa.Column("code", sa.String(16), nullable=False, comment="QLib代码 sh600000"),
            sa.Column("trade_date", sa.Date(), nullable=False, comment="交易日期"),
            sa.Column("pe_ttm", sa.Float(), nullable=True),
            sa.Column("pb_mrq", sa.Float(), nullable=True),
            sa.Column("ps_ttm", sa.Float(), nullable=True),
            sa.Column("pcf_ncf_ttm", sa.Float(), nullable=True),
            sa.PrimaryKeyConstraint("code", "trade_date"),
            sa.Index("idx_fund_code_date", "code", "trade_date"),
        )
