"""把 features/ 下的指数目录注册到 stock_index 主表（一次性种子脚本）。

背景：数据校验/补齐需要区分"指数"与"股票"。指数（sh000001/sz399001...）
由 index_sync 写入 features/，只含 OHLCV 字段，没有 18 个股票 BIN_FIELDS，
也不在 stock_daily / financial_indicator 中，按股票校验会产生大量误报。

本脚本：
1. 确保 stock_index 表存在（Base.metadata.create_all，幂等）
2. 注册 8 大默认指数（config 无自定义清单时 index_sync 使用的清单）
3. 顺带把 features/ 下"未注册但符合指数命名特征"的目录补注册

幂等，可重复执行（ON CONFLICT DO NOTHING）。

用法：
    cd backend && ../.venv/bin/python -m scripts.seed_indices
"""
import asyncio
import os

from app.core.config import settings
from app.core.database import Base, engine
from app.services.data.index_registry import load_index_codes, register_index, register_indices
from app.services.data.index_sync import DEFAULT_INDEX_LIST, INDEX_NAMES


async def main() -> None:
    # 1) 确保表存在（create_all 只建缺失表，幂等）
    import app.models  # noqa: F401  确保所有模型已注册到 metadata
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("create_all 完成（stock_index 表已就绪）")

    # 2) 注册 8 大默认指数
    items = [
        {"code": c, "name": INDEX_NAMES.get(c), "source": "baostock"}
        for c in DEFAULT_INDEX_LIST
    ]
    added = await register_indices(items)
    print(f"注册 8 大默认指数: 新增 {added} 条")

    # 3) features/ 下符合指数命名特征但未注册的目录补注册
    feat = os.path.join(settings.qlib_provider_path, "features")
    existing = await load_index_codes()
    extra = 0
    if os.path.isdir(feat):
        for d in sorted(os.listdir(feat)):
            if d in existing:
                continue
            # 指数段命名特征：sh000*/sz399*（股票段为 sh60/sh68/sh51/sz00/sz30 等）
            if d.startswith(("sh000", "sz399")) and d[2:].isdigit():
                if await register_index(d, INDEX_NAMES.get(d), "akshare"):
                    extra += 1
    print(f"额外补注册指数目录: {extra} 条")

    print("最终指数列表:", sorted(await load_index_codes()))


if __name__ == "__main__":
    asyncio.run(main())
