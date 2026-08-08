"""数据修复：根据校验报告一键补齐 DB 与 qlib 的差异。

修复优先级（前 3 步不消耗 baostock 配额）：
  1. 以 stock_daily 为权威重建 day.txt
  2. 对缺失/错位的股票，直接用 PG stock_daily 重建 qlib bin
  3. 重建 instruments（all/csiall，不覆盖 csi300/csi500）
  4. 仅当 include_baostock=true 且 PG 缺失交易日时，走 baostock 增量回填

修复计划由 run_validation 重新计算；bin 重建只针对确有差异的股票。
"""
import asyncio
import logging
from datetime import date

from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session
from app.models.baostock import StockDaily

from app.services.data.data_clean import format_date_series
from app.services.data.validation import run_validation

logger = logging.getLogger(__name__)

# stock_daily 列 -> _build_out_df 需要的 baostock 风格列名（收敛到 data_fields.py）
from app.services.data.data_fields import STOCK_DB_TO_SRC_COL as _DB_TO_SRC_COL


def _db_rows_to_df(rows: list) -> "object":
    """把 stock_daily 行转成 _build_out_df 输入的 DataFrame（baostock 风格列名）。"""
    import numpy as np
    import pandas as pd

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["date"] = format_date_series(df["trade_date"])
    out = pd.DataFrame({"date": df["date"]})
    for db_col, src_col in _DB_TO_SRC_COL.items():
        if src_col == "isST":
            if db_col in df.columns and df[db_col].notna().any():
                out[src_col] = df[db_col].fillna(False).astype(bool)
            else:
                out[src_col] = pd.Series(np.nan, index=df.index)
        elif db_col in df.columns:
            out[src_col] = df[db_col]
        else:
            out[src_col] = np.nan
    return out


def _rebuild_one_stock(code_upper: str, rows: list, calendar: list, qlib_dir: str) -> None:
    """从 PG 行重建单只股票的全部 bin（线程池中执行）。"""
    import os

    from app.services.data.baostock_backfill import BIN_FIELDS, _build_out_df
    from app.services.data.eod_incremental import _sync_stock_bin

    code_lower = code_upper.lower()
    df = _db_rows_to_df(rows)
    if df.empty:
        return
    out = _build_out_df(code_lower, df)
    feat_dir = os.path.join(qlib_dir, "features", code_lower)
    _sync_stock_bin(feat_dir, out, calendar, BIN_FIELDS, overwrite=True)


# stock_daily 行批量拉取分批大小：控制单批内存（全市场约 5000 只 × 2500 天）
_FETCH_BATCH = 400
# bin 重建并发批大小：一批内并发写盘（线程池 16 workers），批间同步进度
_REBUILD_CONCURRENCY = 64


async def _fetch_stock_rows_bulk(codes: list) -> dict:
    """分批拉取目标股票的全部 stock_daily 行，按 code 分组（大写）。

    消除逐只查询的 N+1 DB 往返（全市场 ~5000 只），同时按批（_FETCH_BATCH）
    拉取，避免全量行一次性进内存（全市场约 1350 万行，分批后峰值内存有界）。
    """
    grouped: dict[str, list] = {}
    if not codes:
        return grouped
    codes_upper = [c.upper() for c in codes]
    for start in range(0, len(codes_upper), _FETCH_BATCH):
        batch = codes_upper[start:start + _FETCH_BATCH]
        stmt = (
            select(*StockDaily.__table__.columns)
            .where(StockDaily.code.in_(batch))
            .order_by(StockDaily.code, StockDaily.trade_date)
        )
        async with async_session() as session:
            rows = [dict(r) for r in (await session.execute(stmt)).mappings().all()]
        for r in rows:
            grouped.setdefault(r["code"], []).append(r)
    return grouped


async def _rebuild_bins_from_pg(codes: list, qlib_dir: str, calendar: list) -> dict:
    """对目标股票从 PG 重建 bin（分批拉取 + 并发写盘）。

    返回:
        dict: {"ok": 重建成功数, "failed": 失败数, "skipped": 无 DB 记录跳过数}
    """
    from concurrent.futures import as_completed

    from app.services.data.baostock_backfill import _get_write_pool
    from app.services.data.sync_progress import update_progress

    if not codes:
        return {"ok": 0, "failed": 0, "skipped": 0}
    grouped = await _fetch_stock_rows_bulk(codes)
    ex = _get_write_pool()
    ok = failed = skipped = 0
    total = len(codes)
    done = 0
    # 批量并发：一次提交 _REBUILD_CONCURRENCY 只，全部完成后算本批结果，
    # 再提交下一批——写盘期间互相重叠，而不是逐只 submit-等待串行化。
    for start in range(0, len(codes), _REBUILD_CONCURRENCY):
        batch = codes[start:start + _REBUILD_CONCURRENCY]
        futures: dict = {}
        for code_upper in batch:
            rows = grouped.get(code_upper.upper())
            if not rows:
                # stock_daily 无此代码（指数目录 / 从未入库）→ 无法从 PG 重建。
                # 记录 warning 而非静默跳过，否则用户会误以为"补齐没生效"。
                skipped += 1
                logger.warning("跳过重建 %s: stock_daily 无记录（可能为指数或数据缺失）", code_upper)
                continue
            fut = ex.submit(_rebuild_one_stock, code_upper, rows, list(calendar), qlib_dir)
            futures[fut] = code_upper
        for fut in as_completed(futures):
            code_upper = futures[fut]
            done += 1
            try:
                fut.result()
                ok += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                logger.warning("重建 %s bin 失败: %s", code_upper, e)
            update_progress(
                pct=30 + int(35 * done / total),
                status="running",
                message=f"从数据库重建 bin {done}/{total}...",
            )
    return {"ok": ok, "failed": failed, "skipped": skipped}


def _compute_years_from_missing(samples: list) -> int:
    """由最早缺失交易日估算 baostock 回填年数（确保窗口覆盖缺失段）。"""
    if not samples:
        return 1
    try:
        earliest = date.fromisoformat(min(samples))
    except ValueError:
        return 1
    return max(1, (date.today() - earliest).days // 365 + 1)


def _recompute_targets(report: dict, calendar_rebuilt: bool,
                       index_codes: set, code_range: dict) -> list:
    """计算需重建 bin 的目标股票，避免补齐时二次全市场扫描。

    - calendar_rebuilt=False：直接复用首次校验（run_validation）两个检查的
      repair_codes 并集——校验与补齐之间没有任何数据变更，结论等价。
    - calendar_rebuilt=True：日历已重建（day.txt 变了），全部 bin 长度必然
      对不上新日历，无需扫描即可判定全市场为目标。

    code_range 为 _load_existing_ranges 返回（小写代码 → [min,max]）。
    """
    if calendar_rebuilt:
        return sorted(c for c in code_range if c not in index_codes)
    fields_codes = set(report["checks"]["fields"].get("repair_codes") or [])
    coverage_codes = set(report["checks"]["coverage"].get("repair_codes") or [])
    return sorted((fields_codes | coverage_codes) - set(index_codes))


async def run_repair(include_baostock: bool = False, universe: str = "all") -> dict:
    """修复主入口（后台任务中执行）。"""
    import os

    from app.services.data.baostock_backfill import (
        _build_instruments,
        _load_existing_ranges,
        rebuild_calendar_from_db,
        run_baostock_backfill,
    )
    from app.services.data.eod_incremental import _get_calendar
    from app.services.data.sync_progress import (
        clear_progress,
        finish_progress,
        init_progress,
        update_progress,
    )

    qlib_dir = settings.qlib_provider_path
    os.makedirs(os.path.join(qlib_dir, "calendars"), exist_ok=True)
    init_progress(universe, "repair", writes_bins=True, kind="repair")

    try:
        update_progress(pct=5, status="running", message="校验并生成修复计划...")
        report = await run_validation(provider_uri=qlib_dir, universe=universe)
        drift = report["drift"]
        steps = []

        # 1. 日历重建（DB 权威，不耗 baostock）
        calendar_rebuilt = bool(drift.get("missing_calendar_days"))
        if calendar_rebuilt:
            update_progress(pct=15, status="running", message="重建 day.txt（来自 stock_daily）...")
            await rebuild_calendar_from_db(qlib_dir)
            steps.append("calendar")

        # 2. 修复目标（不二次全量扫描）：日历未重建时复用首轮校验的 repair_codes；
        #    日历已重建时 bin 长度必然对不上新 day.txt → 全市场都是目标
        #    （剔除指数目录：无 stock_daily 数据，无法从 PG 重建，不应进入
        #    targets 造成"重建了但还报缺失"的假象）
        from app.services.data.index_registry import load_index_codes

        index_codes = await load_index_codes()
        calendar = _get_calendar(qlib_dir)
        code_range = await _load_existing_ranges(qlib_dir, calendar)
        targets = _recompute_targets(report, calendar_rebuilt, index_codes, code_range)
        if targets:
            result = await _rebuild_bins_from_pg(targets, qlib_dir, calendar)
            step = f"bins({result['ok']}ok/{result['failed']}failed"
            if result.get("skipped"):
                step += f"/{result['skipped']}skipped"
            steps.append(step + ")")
        else:
            update_progress(pct=40, status="running", message="bin 无需重建")

        # 3. 日历再次对齐（bin 重建可能引入新日期）
        update_progress(pct=60, status="running", message="重建 day.txt（对齐 bin）...")
        calendar = await rebuild_calendar_from_db(qlib_dir)

        # 4. instruments（all/csiall）
        if code_range:
            _build_instruments(qlib_dir, code_range, calendar, [], [])
            steps.append("instruments")

        # 4.5 宏观/财报字段重广播（日历已最终对齐，bin 长度匹配；尽力而为，失败不阻塞补齐）
        try:
            from app.services.data.macro_sync import broadcast_macro_to_bins
            update_progress(pct=68, status="running", message="重广播宏观字段到 bin（对齐日历）...")
            n = await broadcast_macro_to_bins(
                qlib_dir,
                progress_cb=lambda pct, msg: update_progress(
                    pct=68 + int(10 * (pct - 45) / 55), status="running",
                    message=f"宏观广播: {msg}",
                ),
            )
            if n:
                steps.append(f"macro({n})")
        except Exception as e:  # noqa: BLE001
            logger.warning("宏观字段重广播失败（可稍后在宏观页同步）: %s", e)
        try:
            from app.services.data.fundamental_sync import broadcast_financial_to_bins
            update_progress(pct=80, status="running", message="重广播财报字段到 bin（PIT 对齐）...")
            n = await broadcast_financial_to_bins(
                qlib_dir,
                progress_cb=lambda i, total, msg: update_progress(
                    pct=80 + int(10 * i / max(total, 1)), status="running",
                    message=f"财报广播: {msg}",
                ),
            )
            if n:
                steps.append(f"fin({n})")
        except Exception as e:  # noqa: BLE001
            logger.warning("财报字段重广播失败（可稍后在财报同步页触发）: %s", e)

        # 5. baostock 增量（可选，用户确认）
        if include_baostock and drift.get("needs_baostock"):
            samples = drift.get("pg_missing_date_samples") or []
            years = _compute_years_from_missing(samples)
            update_progress(pct=80, status="running",
                            message=f"从 baostock 补拉缺失交易日（years={years}）...")
            await run_baostock_backfill(years=years, universe=universe, kind="repair")
            steps.append(f"baostock(years={years})")

        # 6. 更新状态
        if calendar:
            from app.services.data.baostock_backfill import _update_sync_status
            await _update_sync_status(universe, qlib_dir, calendar, code_range, sync_path="repair")
        # 把实际修复步骤写进进度 message，前端补齐进度弹窗/进度条可看到
        # （含 bins 的 skipped 计数，解释"跳过 N 只无 DB 记录"）
        if steps:
            update_progress(pct=100, status="running", message=f"修复完成: {', '.join(steps)}")
        finish_progress(True)
        await asyncio.sleep(3)
        clear_progress()

        logger.info("repair 完成: steps=%s", steps)
        return {"ok": True, "steps": steps, "rebuilt_stocks": len(targets)}
    except Exception as e:  # noqa: BLE001
        finish_progress(False, str(e))
        await asyncio.sleep(3)
        clear_progress()
        from app.services.data.baostock_backfill import mark_sync_failed
        await mark_sync_failed(universe, str(e))
        logger.exception("repair 失败")
        return {"ok": False, "error": str(e)}
