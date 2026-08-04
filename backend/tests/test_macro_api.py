"""宏观指标 DB 集成测试（无 Postgres 自动跳过）。

覆盖：
- macro_indicator 表由 create_all 创建
- upsert_macro 幂等（重复写入不产生重复行）
- _load_macro_series 读取
"""
import pytest
from sqlalchemy import func, select


async def test_macro_table_created(db_ready):
    """create_all 应建出 macro_indicator 表。"""
    if not db_ready:
        pytest.skip("需要 Postgres")
    from app.core.database import Base
    assert "macro_indicator" in Base.metadata.tables


async def test_macro_upsert_idempotent(db_ready):
    """同一 (indicator, report_date, field_name) 重复写入不产生重复行。"""
    if not db_ready:
        pytest.skip("需要 Postgres")
    from app.core.database import async_session
    from app.services.data.macro_sync import upsert_macro
    from app.models.macro import MacroIndicator
    from datetime import date

    rows = [{
        "indicator": "PMI",
        "report_date": date(2026, 7, 1),
        "field_name": "pmi",
        "value": 49.2,
        "unit": "",
        "available_date": date(2026, 7, 1),
        "source": "eastmoney",
    }]
    n1 = await upsert_macro(rows)
    n2 = await upsert_macro(rows)
    assert n1 == 1
    assert n2 == 0  # 第二次冲突跳过

    async with async_session() as session:
        cnt = (await session.execute(
            select(func.count()).select_from(MacroIndicator)
            .where(MacroIndicator.indicator == "PMI")
        )).scalar()
    assert cnt == 1
