"""add fundamental_pit table

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-31
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "b2c3d4e5f6g7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


# 新 schema 期望的列集合（baostock 估值字段）
_NEW_COLUMNS = {"code", "trade_date", "pe_ttm", "pb_mrq", "ps_ttm", "pcf_ncf_ttm"}


def _table_exists(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_new_schema(inspector) -> bool:
    """判断 fundamental_pit 是否已是新 schema。

    清理死代码时删除了旧 fundamental 模型，但遗留的旧表（id PK + report_date/
    revenue/net_profit/eps... 等财务字段）未被 drop。本迁移需识别并替换之。
    """
    cols = {c["name"] for c in inspector.get_columns("fundamental_pit")}
    return _NEW_COLUMNS.issubset(cols)


def _create_new_table():
    op.create_table(
        "fundamental_pit",
        sa.Column("code", sa.String(16), nullable=False, comment="QLib代码"),
        sa.Column("trade_date", sa.Date, nullable=False, comment="交易日期"),
        sa.Column("pe_ttm", sa.Float, nullable=True),
        sa.Column("pb_mrq", sa.Float, nullable=True),
        sa.Column("ps_ttm", sa.Float, nullable=True),
        sa.Column("pcf_ncf_ttm", sa.Float, nullable=True),
        sa.PrimaryKeyConstraint("code", "trade_date"),
    )
    op.create_index("idx_fund_code_date", "fundamental_pit", ["code", "trade_date"])


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, "fundamental_pit"):
        # 表不存在 → 直接建新 schema
        _create_new_table()
        return

    if _has_new_schema(inspector):
        # 新 schema 已存在（init_db 的 create_all 可能已建）→ 跳过
        return

    # 旧 schema 残留（清理死代码时未 drop 的遗留表）→ drop 后重建为新 schema
    # 旧表为死代码未接入的数据，rebuild 时替换
    op.drop_table("fundamental_pit")
    _create_new_table()


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _table_exists(inspector, "fundamental_pit"):
        op.drop_index("idx_fund_code_date", table_name="fundamental_pit")
        op.drop_table("fundamental_pit")
