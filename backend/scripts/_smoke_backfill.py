#!/usr/bin/env python
"""冒烟测试：baostock 回填 1 年数据，验证 qlib bin + PG 写入。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main():
    from app.services.data.baostock_backfill import run_baostock_backfill
    result = await run_baostock_backfill(years=1, universe="all")
    print("BACKFILL RESULT:", result)


if __name__ == "__main__":
    asyncio.run(main())
