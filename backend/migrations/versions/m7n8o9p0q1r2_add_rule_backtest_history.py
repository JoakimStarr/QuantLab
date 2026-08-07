"""add rule_backtest_history table

Revision ID: m7n8o9p0q1r2
Revises: l6m7n8o9p0q1
Create Date: 2026-08-07

策略库规则回测历史：每次 /strategy-library/backtest 自动落库（配置 + 结果快照），
前端策略库页面下方列表展示，支持回看详情/一键重跑/删除。

注：新建库会通过 Base.metadata.create_all 自动建表，本迁移仅补已有库。
幂等：用 inspector 检查表是否存在，存在则跳过。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "m7n8o9p0q1r2"
down_revision = "l6m7n8o9p0q1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "rule_backtest_history" in inspector.get_table_names():
        return
    op.create_table(
        "rule_backtest_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("template", sa.String(32), nullable=False),
        sa.Column("template_name", sa.String(64), nullable=False),
        sa.Column("category", sa.String(16), nullable=True),
        sa.Column("kind", sa.String(8), nullable=True),
        sa.Column("params", sa.Text(), nullable=True, comment="配置参数 JSON"),
        sa.Column("symbols", sa.Text(), nullable=False, comment="标的列表 JSON"),
        sa.Column("benchmark", sa.String(16), nullable=True),
        sa.Column("start_date", sa.String(16), nullable=False),
        sa.Column("end_date", sa.String(16), nullable=False),
        sa.Column("annual_return", sa.Float(), nullable=True),
        sa.Column("annual_volatility", sa.Float(), nullable=True),
        sa.Column("sharpe", sa.Float(), nullable=True),
        sa.Column("sortino", sa.Float(), nullable=True),
        sa.Column("max_drawdown", sa.Float(), nullable=True),
        sa.Column("calmar", sa.Float(), nullable=True),
        sa.Column("win_rate", sa.Float(), nullable=True),
        sa.Column("benchmark_return", sa.Float(), nullable=True),
        sa.Column("excess_return", sa.Float(), nullable=True),
        sa.Column("n_trades", sa.Integer(), nullable=True),
        sa.Column("metrics", sa.Text(), nullable=True),
        sa.Column("nav_curve", sa.Text(), nullable=True),
        sa.Column("trades", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("is_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.TIMESTAMP(), nullable=True),
        sa.Index("idx_rbh_created_at", "created_at"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "rule_backtest_history" in inspector.get_table_names():
        op.drop_table("rule_backtest_history")
