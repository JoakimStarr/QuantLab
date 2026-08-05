"""add backtest_result full param snapshot columns

Revision ID: f6a7b8c9d0e1
Revises: e1f2a3b4c5d6
Create Date: 2026-08-05

为 backtest_result 表补充回测参数快照列（combination_method/orthogonalize/benchmark/backend/initial_capital），
使每条回测结果自带当时完整配置，不再依赖 strategy 表的当前值。

幂等：用 inspector 检查列是否存在；已存在历史行从 strategy 表尽力回填
（benchmark/combination_method/orthogonalize），backend 无历史信息保持 NULL。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "f6a7b8c9d0e1"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


_TABLE = "backtest_result"
_STRATEGY_TABLE = "strategy"
_NEW_COLUMNS = [
    ("combination_method", sa.String),
    ("orthogonalize", sa.Integer),
    ("benchmark", sa.String),
    ("backend", sa.String),
    ("initial_capital", sa.Float),
]
# 可尽力回填（strategy 表字段名一致）的列
_BACKFILL_COLUMNS = ["combination_method", "orthogonalize", "benchmark"]


def _column_exists(inspector, table: str, column: str) -> bool:
    if table not in inspector.get_table_names():
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _TABLE not in inspector.get_table_names():
        # 表不存在 → create_all 后续会按新模型建表（含新列），此处跳过
        return

    # 补列
    added = []
    for col_name, col_type in _NEW_COLUMNS:
        if not _column_exists(inspector, _TABLE, col_name):
            op.add_column(_TABLE, sa.Column(col_name, col_type, nullable=True))
            added.append(col_name)

    # 历史行尽力回填：backtest_result 挂在 strategy 上，用策略当前参数填充
    if added and _STRATEGY_TABLE in inspector.get_table_names():
        backfill = [c for c in _BACKFILL_COLUMNS if c in added]
        if backfill:
            cols_sql = ", ".join(
                f"{c} = s.{c}" for c in backfill
            )
            op.execute(
                f"""
                UPDATE {_TABLE} AS br
                SET {cols_sql}
                FROM {_STRATEGY_TABLE} AS s
                WHERE br.strategy_id = s.id AND s.id IS NOT NULL
                """
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _TABLE not in inspector.get_table_names():
        return

    for col_name, _col_type in reversed(_NEW_COLUMNS):
        if _column_exists(inspector, _TABLE, col_name):
            op.drop_column(_TABLE, col_name)
