#!/usr/bin/env python
"""一次性修复脚本：重建丢失的 AutoML 模型 bundle。

data/models/automl/{task_id}.pkl 丢失（目录里只剩 75.pkl），导致引用这些
bundle 的 AutoML 因子在评价/回测时报 "模型文件不存在"。

做法：用原任务的 params（factor_ids/method）重跑 mine_with_automl 重新训练，
并清理重训产生的重复因子（mine_with_automl 每次都会 add_factor 一个新因子），
同时把新模型的指标同步回被策略引用的原因子，保持库内一致。

用法（仓库根目录执行）：
  .venv/bin/python backend/scripts/rebuild_automl_bundles.py
"""
import asyncio
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import async_session  # noqa: E402
from app.models.factor import Factor  # noqa: E402
from app.models.mining_task import MiningTask  # noqa: E402
from app.services.factor.library import update_factor_metrics  # noqa: E402
from app.services.mining.automl import mine_with_automl  # noqa: E402

# (task_id, factor_ids, method, 原因子 id)——原因子即策略实际引用的因子，保留不变
TASKS = [
    (15, [1, 12, 10, 7, 6, 5], "lightgbm", 13),
    (16, [1, 12, 10, 7, 6, 5, 2, 3, 4, 8, 9, 11], "lightgbm", 14),
    (29, [1, 2, 3, 4, 5], "lightgbm", 15),
]


async def restore_result_factor_ids(task_id: int, factor_id: int) -> None:
    """重训会把 task.result_factor_ids 覆盖成新建因子 id，这里还原为原因子 id。"""
    async with async_session() as session:
        t = await session.get(MiningTask, task_id)
        if t is not None:
            t.result_factor_ids = json.dumps([factor_id])
            await session.commit()


async def delete_factor(factor_id: int) -> None:
    """删除重训新建的重复因子（刚创建、无任何引用，可安全删除）。"""
    async with async_session() as session:
        f = await session.get(Factor, factor_id)
        if f is not None:
            await session.delete(f)
            await session.commit()
            print(f"[cleanup] 删除重训产生的重复因子 id={factor_id} name={f.name}")


async def main() -> None:
    for task_id, factor_ids, method, orig_factor_id in TASKS:
        print(f"=== 重训 AutoML task_id={task_id} factor_ids={factor_ids} method={method} ===")
        result = await mine_with_automl(task_id, factor_ids, method)
        new_factor_id = result.get("factor_id")
        ic_metrics = result.get("ic_metrics") or {}
        print(f"    训练完成 new_factor_id={new_factor_id} ic={result.get('ic')}")

        # 新模型的指标同步到原因子，保持因子库指标与 bundle 一致
        if orig_factor_id and ic_metrics:
            await update_factor_metrics(orig_factor_id, ic_metrics)
            print(f"    已同步指标到原因子 id={orig_factor_id} ic={ic_metrics.get('ic')}")

        # 清理重训新建的重复因子，并还原 result_factor_ids
        if new_factor_id:
            await delete_factor(new_factor_id)
        await restore_result_factor_ids(task_id, orig_factor_id)
        print(f"=== task_id={task_id} 完成 ===")


if __name__ == "__main__":
    asyncio.run(main())
