"""add backtest_result param columns for sweep cache

Revision ID: d4e5f6g7h8i9
Revises: c3d4e5f6g7h8
Create Date: 2026-08-01

为 backtest_result 表补充参数列（topk/n_drop/rebalance_freq）+ 扫描查重索引，
支撑参数扫描结果持久化缓存（进程重启后仍可命中 DB 缓存）。

幂等：用 inspector 检查列/索引是否存在。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "d4e5f6g7h8i9"
down_revision = "c3d4e5f6g7h8"
branch_labels = None
depends_on = None


_TABLE = "backtest_result"
_NEW_COLUMNS = [
    ("topk", sa.Integer),
    ("n_drop", sa.Integer),
    ("rebalance_freq", sa.String),
]
_NEW_INDEX = ("idx_backtest_sweep_lookup",
              ["strategy_id", "start_date", "end_date", "topk", "rebalance_freq"])


def _column_exists(inspector, table: str, column: str) -> bool:
    if table not in inspector.get_table_names():
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def _index_exists(inspector, table: str, index_name: str) -> bool:
    if table not in inspector.get_table_names():
        return False
    return index_name in {idx["name"] for idx in inspector.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _TABLE not in inspector.get_table_names():
        # 表不存在 → create_all 后续会按新模型建表（含新列+索引），此处跳过
        return

    # 补列
    for col_name, col_type in _NEW_COLUMNS:
        if not _column_exists(inspector, _TABLE, col_name):
            op.add_column(_TABLE, sa.Column(col_name, col_type, nullable=True))

    # 补扫描查重索引
    idx_name, idx_cols = _NEW_INDEX
    if not _index_exists(inspector, _TABLE, idx_name):
        op.create_index(idx_name, _TABLE, idx_cols)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _TABLE not in inspector.get_table_names():
        return

    idx_name, _idx_cols = _NEW_INDEX
    if _index_exists(inspector, _TABLE, idx_name):
        op.drop_index(idx_name, table_name=_TABLE)

    for col_name, _col_type in reversed(_NEW_COLUMNS):
        if _column_exists(inspector, _TABLE, col_name):
            op.drop_column(_TABLE, col_name)
