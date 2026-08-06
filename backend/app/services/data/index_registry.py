"""指数主数据注册/查询（stock_index 表）。

数据校验/补齐用它区分"指数目录"与"股票目录"：指数只写 OHLCV，
没有 18 个股票 BIN_FIELDS，也没有 stock_daily / 财报数据，不应按股票校验。

所有函数都是 async（访问 PostgreSQL），无 baostock 配额消耗。
"""
import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.database import async_session
from app.models.stock_index import StockIndex

logger = logging.getLogger(__name__)


async def register_index(code: str, name: str | None = None, source: str | None = None) -> bool:
    """注册一个指数（幂等：已存在则忽略）。

    Args:
        code: qlib 指数代码，小写（如 sh000001）
        name: 指数名称（如 上证指数）
        source: 数据源（baostock/akshare）

    Returns:
        bool: True 表示新增，False 表示已存在
    """
    code = (code or "").strip().lower()
    if not code:
        return False
    stmt = pg_insert(StockIndex).values(code=code, name=name, source=source)
    stmt = stmt.on_conflict_do_nothing(index_elements=["code"])
    async with async_session() as session:
        res = await session.execute(stmt)
        await session.commit()
        inserted = (res.rowcount or 0) > 0
    if inserted:
        logger.info("已注册指数 %s (%s, source=%s)", code, name, source)
    return inserted


async def register_indices(items: list[dict]) -> int:
    """批量注册指数（幂等）。

    Args:
        items: [{"code": "sh000001", "name": "上证指数", "source": "baostock"}]

    Returns:
        int: 本次新增条数
    """
    added = 0
    for it in items:
        if await register_index(it.get("code"), it.get("name"), it.get("source")):
            added += 1
    return added


async def sync_and_register_indices(provider_uri: str, days: int = 365) -> dict:
    """同步指数并注册到 stock_index（供 sync_worker / full_sync 复用）。

    Returns:
        dict: sync_indices_to_qlib 的结果（ok/success/failed/indices/source/...）。
    """
    from app.services.data.index_sync import INDEX_NAMES, sync_indices_to_qlib

    result = sync_indices_to_qlib(provider_uri, days=days)
    if result.get("ok"):
        items = [
            {"code": c, "name": INDEX_NAMES.get(c), "source": result.get("source") or "baostock"}
            for c in result.get("indices") or []
        ]
        try:
            n = await register_indices(items)
            if n:
                logger.info("已注册 %d 个新指数到 stock_index", n)
        except Exception as e:  # noqa: BLE001
            logger.warning("注册指数到 stock_index 失败: %s", e)
    else:
        logger.error("指数同步返回错误: %s", result)
    return result


async def load_index_codes() -> set[str]:
    """返回全部已注册指数/ETF 代码集合（小写，如 {'sh000001', 'sh510300'}）。

    validation/repair 用它排除"非股票"目录——ETF 注册进同表后自动被排除，
    无需改动校验/修复逻辑。
    """
    async with async_session() as session:
        rows = await session.execute(select(StockIndex.code))
        return {r[0].lower() for r in rows}


async def load_etf_codes() -> set[str]:
    """返回已注册 ETF 代码集合（小写，type='etf'）。"""
    async with async_session() as session:
        rows = await session.execute(
            select(StockIndex.code).where(StockIndex.type == "etf")
        )
        return {r[0].lower() for r in rows}


async def register_etf(code: str, name: str | None = None, source: str | None = None) -> bool:
    """注册一个 ETF（幂等：已存在则忽略），type='etf'。

    Args:
        code: qlib ETF 代码，小写（如 sh510300）
        name: ETF 名称（如 沪深300ETF）
        source: 数据源（baostock/akshare）

    Returns:
        bool: True 表示新增，False 表示已存在
    """
    code = (code or "").strip().lower()
    if not code:
        return False
    stmt = pg_insert(StockIndex).values(code=code, name=name, source=source, type="etf")
    stmt = stmt.on_conflict_do_nothing(index_elements=["code"])
    async with async_session() as session:
        res = await session.execute(stmt)
        await session.commit()
        inserted = (res.rowcount or 0) > 0
    if inserted:
        logger.info("已注册 ETF %s (%s, source=%s)", code, name, source)
    return inserted


async def load_index_map() -> dict[str, dict]:
    """返回 code -> {name, source} 映射（小写 code）。"""
    async with async_session() as session:
        rows = await session.execute(select(StockIndex.code, StockIndex.name, StockIndex.source))
        return {r[0].lower(): {"name": r[1], "source": r[2]} for r in rows}
