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


async def run_full_sync(years: int, universe: str = "all", refresh_misc: bool = False) -> dict:
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

        backfill = await run_baostock_backfill(years=years, universe=universe,
                                               refresh_misc=refresh_misc)
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

        # 阶段 4-6/6: 宏观 / 财报 / 外盘 —— 三者都不连 baostock（无并发连接限制），
        # 并行下载拉取（akshare/eastmoney 各自独立，互不依赖）。
        # 并发约束：它们都会写 bin，但写的是**不同字段文件**（$pmi/$roe/$us_*_ret），
        # 互不冲突；共享进度文件由本函数统一管理，各阶段通过 progress_cb 上报，
        # 避免多个并行阶段互相 init/finish/clear 进度造成竞态。
        _restage(56, "阶段4-6/6: 宏观/财报/外盘 并行同步+广播...")
        from app.services.data.macro_sync import sync_macro_indicators
        from app.services.data.fundamental_sync import run_financial_sync
        from app.services.data.external_market import sync_external_market

        def _cb(base: float, span: float, label: str):
            def cb(pct: float, msg: str) -> None:
                update_progress(pct=base + span * pct / 100.0, status="running",
                                message=f"{label}: {msg}")
            return cb

        # 财报的进度回调签名是 (i, n, msg)——按计数上报，这里换算成百分比
        def _cb_fin(base: float, span: float):
            def cb(i: int, n: int, msg: str) -> None:
                update_progress(pct=base + span * i / max(n, 1), status="running",
                                message=f"财报: {msg}")
            return cb

        # 并行执行：macro(56-72) / financial(56-86，大头) / external(56-64)
        fin_task = asyncio.create_task(run_financial_sync(broadcast=True, progress_cb=_cb_fin(56, 30)))
        macro_task = asyncio.create_task(sync_macro_indicators(broadcast=True, progress_cb=_cb(56, 10, "宏观")))
        ext_task = asyncio.create_task(sync_external_market())
        fin = await fin_task
        macro = await macro_task
        ext = await ext_task
        steps.append(f"macro({macro.get('inserted', 0)}rows/{macro.get('fields_written', 0)}fields)")
        steps.append(f"fin({fin.get('fetched', 0)}fetched/{fin.get('inserted', 0)}rows)")
        steps.append("external")
        logger.info("阶段4-6/6 完成: macro=%s fin=%s ext=%s", macro, fin, ext)

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
