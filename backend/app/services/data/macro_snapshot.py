"""宏观指标快照：每个 (indicator, field_name) 返回最新一条 + 环比所需的上一条。

原 SQL 内嵌在 api/macro.py 的 macro_snapshot_api，抽取为共享服务函数，
供宏观接口与每日晨报（daily_report）复用。
"""
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

# 窗口 SQL：按 (indicator, field_name) 分区取最近两行（available_date 倒序），
# rn=1 为最新值，rn=2 为前值，供环比计算。
_SNAPSHOT_SQL = text("""
    SELECT indicator, field_name,
           MAX(CASE WHEN rn = 1 THEN unit END) AS unit,
           MAX(CASE WHEN rn = 1 THEN available_date END) AS latest_date,
           MAX(CASE WHEN rn = 1 THEN value END) AS latest_value,
           MAX(CASE WHEN rn = 2 THEN available_date END) AS prev_date,
           MAX(CASE WHEN rn = 2 THEN value END) AS prev_value
    FROM (
        SELECT indicator, field_name, unit, available_date, value,
               ROW_NUMBER() OVER (
                   PARTITION BY indicator, field_name
                   ORDER BY available_date DESC
               ) AS rn
        FROM macro_indicator
    ) t
    WHERE rn <= 2
    GROUP BY indicator, field_name
    ORDER BY indicator, field_name
""")


async def get_macro_snapshot(db) -> list[dict]:
    """返回宏观指标快照列表（每个 (indicator, field_name) 一条）。

    Args:
        db: 异步 SQLAlchemy session。
    Returns:
        [{indicator, field_name, unit, latest_date, latest_value, prev_date, prev_value}, ...]
    """
    rows = await db.execute(_SNAPSHOT_SQL)
    return [{
        "indicator": r.indicator,
        "field_name": r.field_name,
        "unit": r.unit,
        "latest_date": r.latest_date.isoformat() if r.latest_date else None,
        "latest_value": r.latest_value,
        "prev_date": r.prev_date.isoformat() if r.prev_date else None,
        "prev_value": r.prev_value,
    } for r in rows]
