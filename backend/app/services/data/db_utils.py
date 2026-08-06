"""数据库批量写入公共工具：幂等批量 upsert。

收拢各同步模块里重复的 ``async_session + pg_insert + on_conflict + 分块 + commit``
模式（stock_daily / etf_daily / macro_indicator / financial_indicator 等窄表）。
"""
from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.database import async_session


async def bulk_upsert(model, rows: list[dict], keys: list[str],
                      batch: int = 500, update_cols: list[str] | None = None) -> int:
    """批量幂等写入（``ON CONFLICT DO NOTHING`` / ``DO UPDATE``）。

    Args:
        model: SQLAlchemy 表模型（用 ``model.__table__`` 定位表）。
        rows: 待写入的 dict 列表。
        keys: 冲突键列名（唯一约束列）。
        batch: 每批行数，防 asyncpg 单条 SQL 参数上限（32767）。
        update_cols: 提供则冲突时用 ``DO UPDATE`` 更新这些列；否则 ``DO NOTHING``。

    Returns:
        受影响行数（``DO NOTHING`` 时即实际插入行数）。
    """
    if not rows:
        return 0
    affected = 0
    async with async_session() as session:
        for i in range(0, len(rows), batch):
            chunk = rows[i:i + batch]
            stmt = pg_insert(model.__table__).values(chunk)
            if update_cols:
                stmt = stmt.on_conflict_do_update(
                    index_elements=keys,
                    set_={k: getattr(stmt.excluded, k) for k in update_cols},
                )
            else:
                stmt = stmt.on_conflict_do_nothing(index_elements=keys)
            res = await session.execute(stmt)
            affected += res.rowcount or 0
        await session.commit()
    return affected
