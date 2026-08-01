"""add composite indexes for query optimization

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6g7
Create Date: 2026-08-01

为已有表补充复合索引（v2.4.0 性能优化）：
  - mining_task(status, created_at): 任务列表高频查询
  - backtest_result(strategy_id, start_date, end_date): 参数扫描/历史查重
  - sync_history(universe, started_at): 按股票池查最近同步记录
  - factor(source_task_id): 按挖掘任务反查因子

注：新建库会通过 Base.metadata.create_all 自动建索引，本迁移仅补已有库。
幂等：用 inspector 检查索引是否存在，存在则跳过。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "c3d4e5f6g7h8"
down_revision = "b2c3d4e5f6g7"
branch_labels = None
depends_on = None


# (table, index_name, columns) 列表
_INDEXES = [
    ("mining_task", "idx_mining_status_created", ["status", "created_at"]),
    ("backtest_result", "idx_backtest_strategy_period",
     ["strategy_id", "start_date", "end_date"]),
    ("sync_history", "idx_sync_history_universe_started",
     ["universe", "started_at"]),
    ("factor", "idx_factor_source_task", ["source_task_id"]),
]


def _index_exists(inspector, table: str, index_name: str) -> bool:
    """检查索引是否已存在（兼容已通过 create_all 建过的新库）。"""
    if table not in inspector.get_table_names():
        return False
    existing = {idx["name"] for idx in inspector.get_indexes(table)}
    return index_name in existing


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table, index_name, columns in _INDEXES:
        if _index_exists(inspector, table, index_name):
            # 已存在（新库 create_all 已建）→ 跳过
            continue
        if table not in inspector.get_table_names():
            # 表不存在 → 跳过（create_all 后续会一并建表+索引）
            continue
        op.create_index(index_name, table, columns)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table, index_name, _columns in reversed(_INDEXES):
        if not _index_exists(inspector, table, index_name):
            continue
        op.drop_index(index_name, table_name=table)
