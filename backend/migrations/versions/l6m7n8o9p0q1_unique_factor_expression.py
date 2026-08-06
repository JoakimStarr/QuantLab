"""add unique index on factor.expression

Revision ID: l6m7n8o9p0q1
Revises: k5l6m7n8o9p0
Create Date: 2026-08-06

同一因子表达式只允许入库一次（防挖掘/手动重复新增）。
前置条件：库中已有重复表达式需先清理（本地已清理：13 条重复 test_factor
已删除，159 因子全部唯一）；若其他环境仍存在重复，索引创建会失败并提示。

幂等：用 inspector 检查索引是否存在，存在则跳过。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "l6m7n8o9p0q1"
down_revision = "k5l6m7n8o9p0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {i["name"] for i in inspector.get_indexes("factor")}
    if "uq_factor_expression" not in indexes:
        op.create_index("uq_factor_expression", "factor", ["expression"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {i["name"] for i in inspector.get_indexes("factor")}
    if "uq_factor_expression" in indexes:
        op.drop_index("uq_factor_expression", table_name="factor")
