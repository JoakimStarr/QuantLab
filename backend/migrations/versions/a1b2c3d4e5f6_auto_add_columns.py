"""auto add missing columns

Revision ID: a1b2c3d4e5f6
Revises: 23fc4c667c2f
Create Date: 2026-07-30
"""
from alembic import op

# revision identifiers
revision = "a1b2c3d4e5f6"
down_revision = "23fc4c667c2f"
branch_labels = None
depends_on = None


def _add_column_if_not_exists(table: str, column: str, ddl_type: str) -> None:
    """SQLite 3.35+ 支持 ADD COLUMN；通过 PRAGMA 检测列是否存在。"""
    bind = op.get_bind()
    res = bind.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in res.fetchall()}
    if column not in existing:
        bind.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")


def upgrade() -> None:
    # 历史模型新增字段在此声明，老库自动补列
    # 示例（按需取消注释或追加）：
    # _add_column_if_not_exists("factor", "owner", "VARCHAR")
    pass


def downgrade() -> None:
    pass
