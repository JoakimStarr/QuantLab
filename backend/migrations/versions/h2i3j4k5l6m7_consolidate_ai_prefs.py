"""consolidate strategy AI prefs into ai_prefs JSON column

Revision ID: h2i3j4k5l6m7
Revises: g1h2i3j4k5l6
Create Date: 2026-08-06

AI 生成偏好统一存为一个 JSON 字段 strategy.ai_prefs
（{style, risk_tolerance, rebalance_pref, capital, other}），
取代上一版单独拆出的 strategy.capital 列 + description 标签双存。

步骤：加 ai_prefs → 回填已有 capital → 删 capital 列。
幂等：用 inspector 检查列是否存在，存在则跳过。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "h2i3j4k5l6m7"
down_revision = "g1h2i3j4k5l6"
branch_labels = None
depends_on = None


def _column_exists(inspector, table: str, column: str) -> bool:
    if table not in inspector.get_table_names():
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _column_exists(inspector, "strategy", "ai_prefs"):
        op.add_column("strategy", sa.Column("ai_prefs", sa.Text(), nullable=True))

    # 回填：已存在的 capital 值并入 ai_prefs JSON，再删除 capital 列
    if _column_exists(inspector, "strategy", "capital"):
        op.execute(
            "UPDATE strategy SET ai_prefs = "
            "json_build_object('capital', capital) "
            "WHERE capital IS NOT NULL"
        )
        op.drop_column("strategy", "capital")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _column_exists(inspector, "strategy", "capital"):
        op.add_column("strategy", sa.Column("capital", sa.Float(), nullable=True))
        op.execute(
            "UPDATE strategy SET capital = (ai_prefs->>'capital')::float "
            "WHERE ai_prefs IS NOT NULL AND ai_prefs->>'capital' IS NOT NULL"
        )

    if _column_exists(inspector, "strategy", "ai_prefs"):
        op.drop_column("strategy", "ai_prefs")
