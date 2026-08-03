#!/usr/bin/env python
"""一次性脚本：将 SQLite 本地数据（data/quantlab.db）迁移到本地 PostgreSQL。

用法（从仓库根目录执行）：
  export POSTGRES_USER=quantlab POSTGRES_PASSWORD=quantlab POSTGRES_DB=quantlab
  export POSTGRES_HOST=localhost POSTGRES_PORT=5432
  .venv/bin/python backend/scripts/migrate_sqlite_to_pg.py

说明：
  - 按 SQLite 与 Postgres 的公共列逐表复制（SQLite 缺少的新列交给建表默认值）。
  - 幂等：INSERT ... ON CONFLICT DO NOTHING，可重复执行。
  - 复制后重置 id 自增序列，避免后续插入主键冲突。
"""

import os
import sqlite3
import sys
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

SQLITE_DB = os.getenv("SQLITE_DB", "data/quantlab.db")

# 满足 FK 依赖（backtest_result.strategy_id -> strategy.id）的插入顺序
PREFERRED_ORDER = [
    "strategy",
    "factor",
    "mining_task",
    "stock_data_status",
    "sync_history",
    "fundamental_pit",
    "backtest_result",
    "task_result",
]

CHUNK_SIZE = 500


def parse_ts(value):
    """SQLite 的 ISO 字符串时间戳 -> datetime；非时间戳/已转换的值原样返回。"""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            return value
    return value


def main() -> int:
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        if key not in os.environ:
            print(f"[error] 缺少环境变量 {key}")
            return 1

    url = (
        f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@{os.environ.get('POSTGRES_HOST', 'localhost')}:{os.environ.get('POSTGRES_PORT', '5432')}"
        f"/{os.environ['POSTGRES_DB']}"
    )
    pg = create_engine(url)
    pg_insp = inspect(pg)

    src = sqlite3.connect(SQLITE_DB)
    src.row_factory = sqlite3.Row

    sqlite_tables = [
        r[0]
        for r in src.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version' "
            "ORDER BY name"
        )
    ]
    # 先按首选顺序，再补未知表
    tables = [t for t in PREFERRED_ORDER if t in sqlite_tables] + [
        t for t in sqlite_tables if t not in PREFERRED_ORDER
    ]

    total_rows = 0
    with pg.begin() as conn:
        for table in tables:
            if table not in pg_insp.get_table_names():
                print(f"[skip] {table}: 不在 Postgres 中")
                continue

            sqlite_cols = [
                r["name"] for r in src.execute(f'PRAGMA table_info("{table}")')
            ]
            pg_cols = [c["name"] for c in pg_insp.get_columns(table)]
            common = [c for c in sqlite_cols if c in pg_cols]
            if not common:
                print(f"[skip] {table}: 无公共列")
                continue

            # SQLite 缺失、但 Postgres 中 NOT NULL 且无 server default 的列 → 补类型默认值
            missing_defaults = {}
            for c in pg_insp.get_columns(table):
                if c["name"] in common or c["nullable"]:
                    continue
                if c["default"] is not None:
                    continue  # 有 server default，Postgres 会自动填充
                t = c["type"].__class__.__name__
                if t in ("INTEGER", "BIGINT", "SMALLINT"):
                    missing_defaults[c["name"]] = 0
                elif t in ("FLOAT", "REAL", "NUMERIC", "DOUBLE", "DOUBLE_PRECISION"):
                    missing_defaults[c["name"]] = 0.0
                elif t == "BOOLEAN":
                    missing_defaults[c["name"]] = False
                elif t in ("VARCHAR", "TEXT", "CHAR", "STRING"):
                    missing_defaults[c["name"]] = ""
                else:
                    raise SystemExit(
                        f"[error] {table}.{c['name']} 非空且无默认值（类型 {t}），需人工处理"
                    )

            ts_cols = {
                c["name"]
                for c in pg_insp.get_columns(table)
                if c["type"].__class__.__name__ in ("TIMESTAMP", "TIMESTAMPWithoutTimeZone")
            }

            rows = src.execute(f'SELECT * FROM "{table}"').fetchall()
            if not rows:
                print(f"[ok]   {table}: 0 行")
                continue

            all_cols = common + list(missing_defaults.keys())
            tbl = sa.table(table, *[sa.column(c) for c in all_cols])
            inserted = 0
            for i in range(0, len(rows), CHUNK_SIZE):
                chunk = rows[i : i + CHUNK_SIZE]
                payload = []
                for row in chunk:
                    rec = {c: row[c] for c in common}
                    rec.update(missing_defaults)
                    for tc in ts_cols:
                        if tc in rec:
                            rec[tc] = parse_ts(rec[tc])
                    payload.append(rec)
                stmt = (
                    pg_insert(tbl)
                    .values(payload)
                    .on_conflict_do_nothing()
                    .returning(tbl.c.id)
                )
                inserted += len(list(conn.execute(stmt)))

            # 重置 id 自增序列
            if "id" in common:
                seq = conn.execute(
                    text(f"SELECT pg_get_serial_sequence('{table}', 'id')")
                ).scalar()
                if seq:
                    max_id = conn.execute(
                        text(f'SELECT COALESCE(MAX(id), 0) FROM "{table}"')
                    ).scalar()
                    conn.execute(text(f"SELECT setval('{seq}', {max_id})"))

            total_rows += inserted
            print(f"[ok]   {table}: 源 {len(rows)} 行，插入 {inserted} 行")

    print(f"\n完成：共迁移 {total_rows} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
