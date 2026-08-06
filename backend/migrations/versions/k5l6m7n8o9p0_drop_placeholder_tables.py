"""drop schema-only placeholder tables (fin_* + margin_daily)

Revision ID: k5l6m7n8o9p0
Revises: j4k5l6m7n8o9
Create Date: 2026-08-06

以下 7 张表为早期设计占位，0 行、无任何业务代码读写（季频财报已由
financial_indicator 窄表覆盖；日频估值在 stock_daily；融资融券未接入）：
  fin_profit / fin_operation / fin_growth / fin_balance / fin_cashflow /
  fin_dupont / margin_daily
删除以收敛 schema（与 fundamental_pit 同批清理）。

幂等：用 inspector 检查表是否存在，存在则删。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "k5l6m7n8o9p0"
down_revision = "j4k5l6m7n8o9"
branch_labels = None
depends_on = None

_DROP_TABLES = [
    "fin_profit", "fin_operation", "fin_growth", "fin_balance",
    "fin_cashflow", "fin_dupont", "margin_daily",
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    for t in _DROP_TABLES:
        if t in existing:
            op.drop_table(t)


def downgrade() -> None:
    # 占位表重建（保留最简结构；原列定义见迁移历史/旧模型，此处仅恢复骨架）
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    if "fin_profit" not in existing:
        op.create_table(
            "fin_profit",
            sa.Column("code", sa.String(16), nullable=False),
            sa.Column("stat_date", sa.Date(), nullable=False),
            sa.Column("pub_date", sa.Date(), nullable=False),
            sa.PrimaryKeyConstraint("code", "stat_date", "pub_date"),
        )
    for t in ["fin_operation", "fin_growth", "fin_balance", "fin_cashflow",
              "fin_dupont", "margin_daily"]:
        if t not in existing:
            op.create_table(
                t,
                sa.Column("code", sa.String(16), nullable=False),
                sa.Column("stat_date", sa.Date(), nullable=False),
                sa.Column("pub_date", sa.Date(), nullable=False),
                sa.PrimaryKeyConstraint("code", "stat_date", "pub_date"),
            )
