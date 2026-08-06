"""一键全同步：按依赖顺序串联全部数据线路（独立 worker 子进程执行）。

顺序约束（bin 必须对齐最终日历 day.txt，否则 qlib 读位错位、因子全 NaN）：
  1. A股回填（baostock）——确立/扩展 day.txt，写股票 OHLCV
  2. 指数同步（baostock/akshare）——写指数目录，注册 stock_index
  3. ETF 同步（baostock）——全市场 ETF 日K + 全量池
  4. 宏观指标 拉取+广播（东财/akshare）
  5. 财报 拉取(增量)+广播（akshare，PIT forward-fill）
  6. 外盘数据 拉取+广播（akshare）

并发约束：
- baostock 有爬取锁（kind=full 走锁），回填/指数/ETF 串行；宏观/财报/外盘不走该锁。
- 各阶段内部自行管理进度生命周期（init→finish→clear），因此每阶段结束后
  重新 init_progress 恢复"一键全同步"的进度标识。

时长提示：首次 A股回填+财报全量可达数小时；财报是增量的，已入库的股票跳过。
"""
import asyncio
import logging
import os

from app.core.config import settings
from app.services.data.sync_progress import (
    clear_progress,
    finish_progress,
    init_progress,
    set_worker_pid,
    update_progress,
)

logger = logging.getLogger(__name__)


async def run_full_sync(years: int, universe: str = "all") -> dict:
    """一键全同步主入口（worker 子进程中执行）。"""
    qlib_dir = settings.qlib_provider_path
    init_progress(universe, "full", writes_bins=True, kind="full")
    steps: list[str] = []

    def _restage(pct: float, message: str) -> None:
        # 前一阶段内部已 finish+clear，重新恢复全同步进度标识。
        # 必须重设 worker_pid：clear 后 init_progress 读不到存活 pid，
        # 若不重设，worker 中途死亡时 sync_is_active 无法识别僵尸进度，
        # 会长期阻塞后续同步/补齐。
        init_progress(universe, "full", writes_bins=True, kind="full")
        set_worker_pid(os.getpid())
        update_progress(pct=pct, status="running", message=message)

    try:
        # 阶段 1/6: A股回填（含外盘/宏观按最终日历重广播）
        update_progress(pct=4, status="running",
                        message=f"阶段1/6: baostock A股回填 {years} 年...")
        from app.services.data.baostock_backfill import run_baostock_backfill

        backfill = await run_baostock_backfill(years=years, universe=universe)
        steps.append(f"a-share({backfill.get('stocks', 0)}stocks)")
        logger.info("阶段1/6 完成: %s", backfill)

        # 阶段 2/6: 指数同步（baostock/akshare，自动注册 stock_index）
        _restage(32, "阶段2/6: 指数同步（8大指数，注册 stock_index）...")
        from app.services.data.index_registry import sync_and_register_indices

        idx = await sync_and_register_indices(qlib_dir)
        steps.append(f"indices({idx.get('success', 0)}ok/{idx.get('failed', 0)}fail)")
        logger.info("阶段2/6 完成: %s", idx)

        # 阶段 3/6: ETF 同步 + 全量池
        _restage(42, "阶段3/6: ETF 同步（全市场日K，重建全量池）...")
        from app.services.data.etf_sync import sync_etf_task

        etf = await sync_etf_task(qlib_dir, days=730)
        steps.append(f"etf({etf.get('etf_count', 0)}etfs/{etf.get('pool_count', 0)}pool)")
        logger.info("阶段3/6 完成: %s", etf)

        # 阶段 4/6: 宏观指标 拉取 + 广播
        _restage(56, "阶段4/6: 宏观指标同步+广播（PMI/CPI/利率/汇率等）...")
        from app.services.data.macro_sync import run_macro_sync_task

        await run_macro_sync_task(broadcast=True)
        steps.append("macro")
        logger.info("阶段4/6 完成: 宏观同步+广播")

        # 阶段 5/6: 财报 拉取(增量) + PIT 广播
        _restage(68, "阶段5/6: 财报同步（增量拉取）+广播...")
        from app.services.data.fundamental_sync import run_financial_sync

        fin = await run_financial_sync(broadcast=True)
        steps.append(f"fin({fin.get('fetched', 0)}fetched/{fin.get('inserted', 0)}rows)")
        logger.info("阶段5/6 完成: %s", fin)

        # 阶段 6/6: 外盘数据 拉取 + 广播
        _restage(88, "阶段6/6: 外盘隔夜情绪因子同步...")
        from app.services.data.external_market import sync_external_market

        ext = await sync_external_market()
        steps.append("external")
        logger.info("阶段6/6 完成: %s", ext)

        update_progress(pct=100, status="running", message=f"全同步完成: {', '.join(steps)}")
        finish_progress(True)
        await asyncio.sleep(3)
        clear_progress()
        logger.info("full 同步完成: steps=%s", steps)
        return {"ok": True, "steps": steps}
    except Exception as e:  # noqa: BLE001
        finish_progress(False, str(e))
        await asyncio.sleep(3)
        clear_progress()
        from app.services.data.baostock_backfill import mark_sync_failed

        await mark_sync_failed(universe, str(e))
        logger.exception("full 同步失败")
        return {"ok": False, "error": str(e)}
