"""add stock_index.type column and etf_daily table

Revision ID: i3j4k5l6m7n8
Revises: h2i3j4k5l6m7
Create Date: 2026-08-06

ETF 标的池：
- stock_index.type：'index'（默认）/ 'etf'，复用同一注册表，validation/repair
  经 load_index_codes() 一并排除，无需改校验/修复逻辑。
- etf_daily 窄表：ETF 日K（code/trade_date/OHLCV/volume/amount/pct_chg），
  供精选流动池筛选与未来 repair 重建；不混入 stock_daily。

注：新建库会通过 Base.metadata.create_all 自动建列/建表，本迁移仅补已有库。
幂等：用 inspector 检查存在性，存在则跳过。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "i3j4k5l6m7n8"
down_revision = "h2i3j4k5l6m7"
branch_labels = None
depends_on = None


def _column_exists(inspector, table: str, column: str) -> bool:
    if table not in inspector.get_table_names():
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _column_exists(inspector, "stock_index", "type"):
        op.add_column(
            "stock_index",
            sa.Column("type", sa.String(16), nullable=False, server_default="index"),
        )

    if "etf_daily" not in inspector.get_table_names():
        op.create_table(
            "etf_daily",
            sa.Column("code", sa.String(16), nullable=False, comment="QLib代码 sh510300"),
            sa.Column("trade_date", sa.Date(), nullable=False, comment="交易日期"),
            sa.Column("open", sa.Float(), nullable=True),
            sa.Column("high", sa.Float(), nullable=True),
            sa.Column("low", sa.Float(), nullable=True),
            sa.Column("close", sa.Float(), nullable=True),
            sa.Column("volume", sa.Float(), nullable=True, comment="成交量(份)"),
            sa.Column("amount", sa.Float(), nullable=True, comment="成交额(元)"),
            sa.Column("pct_chg", sa.Float(), nullable=True, comment="涨跌幅(%)"),
            sa.PrimaryKeyConstraint("code", "trade_date"),
            sa.Index("idx_etf_daily_date", "trade_date"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "etf_daily" in inspector.get_table_names():
        op.drop_table("etf_daily")

    if _column_exists(inspector, "stock_index", "type"):
        op.drop_column("stock_index", "type")
