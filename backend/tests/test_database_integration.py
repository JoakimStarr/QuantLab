"""Postgres 集成测试：验证数据库连接 + 表创建 + 简单 CRUD。

依赖 Postgres（CI: postgres:5432 service；本地：用户自起 docker compose postgres）。
无 DB 时所有测试自动跳过，不影响 CI 通过率。
"""

import pytest
from sqlalchemy import select


async def test_engine_creates_tables(db_ready):
    """create_all 应至少建出 factor / strategy 等关键表。"""
    if not db_ready:
        pytest.skip("需要 Postgres")
    from app.core.database import Base, engine

    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda sync_conn: set(Base.metadata.tables.keys()))
    assert "factor" in tables
    assert "strategy" in tables


async def test_health_check_ok(db_ready):
    """健康检查应返回 ok。"""
    if not db_ready:
        pytest.skip("需要 Postgres")
    from app.core.database import health_check

    result = await health_check()
    assert result["status"] == "ok"


async def test_factor_crud_roundtrip(db_ready):
    """向 factor 表 insert 一行 + select 回读。

    该测试固定插入 name/expression 相同的行且不清理，第二次运行会撞
    uq_factor_expression 唯一约束 → 开头先删除上次残留，保证可重复运行。
    """
    if not db_ready:
        pytest.skip("需要 Postgres")
    from app.core.database import async_session
    from app.models.factor import Factor

    async with async_session() as session:
        stale = await session.execute(select(Factor).where(Factor.expression == "Mean($close, 5)"))
        for row in stale.scalars():
            await session.delete(row)
        await session.commit()

    async with async_session() as session:
        rec = Factor(
            name="test_factor",
            expression="Mean($close, 5)",
            category="builtin",
            description="测试因子",
        )
        session.add(rec)
        await session.commit()
        await session.refresh(rec)
        assert rec.id is not None

        result = await session.execute(select(Factor).where(Factor.id == rec.id))
        got = result.scalar_one()
        assert got.expression == "Mean($close, 5)"
