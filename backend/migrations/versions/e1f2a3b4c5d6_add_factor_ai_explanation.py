"""add factor ai_explanation & ai_chat_history columns

Revision ID: e1f2a3b4c5d6
Revises: b5e6f7g8h9i0
Create Date: 2026-08-05

AI 因子解释升级：
  - factor.ai_explanation：完整解释的结构化 JSON（summary/logic/rationale/caveats/generated_at）
  - factor.ai_chat_history：追问对话历史 JSON 数组

注：新建库会通过 Base.metadata.create_all 自动建列，本迁移仅补已有库。
幂等：用 inspector 检查列是否存在，存在则跳过。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "e1f2a3b4c5d6"
down_revision = "b5e6f7g8h9i0"
branch_labels = None
depends_on = None


def _column_exists(inspector, table: str, column: str) -> bool:
    if table not in inspector.get_table_names():
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _column_exists(inspector, "factor", "ai_explanation"):
        op.add_column("factor", sa.Column("ai_explanation", sa.Text(), nullable=True))
    if not _column_exists(inspector, "factor", "ai_chat_history"):
        op.add_column("factor", sa.Column("ai_chat_history", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _column_exists(inspector, "factor", "ai_chat_history"):
        op.drop_column("factor", "ai_chat_history")
    if _column_exists(inspector, "factor", "ai_explanation"):
        op.drop_column("factor", "ai_explanation")
