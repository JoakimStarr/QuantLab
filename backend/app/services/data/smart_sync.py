"""智能同步编排器：按 latest_date 距今天数自动选择同步路径。

路径判断（替代 sync_runner._dispatch_sync 的多源回退逻辑）：
  - qlib_dir 不存在 或 day.txt 缺失 → chenditc 全量初始化
  - latest_date 距今 > full_sync_threshold_days(默认 7) → chenditc 全量
  - latest_date 距今 1-7 天 → baostock 增量补缺失日期(含当日)
  - latest_date 是今天(距今 0 天) → baostock 同步当日(含盘中)

阈值与开关由 config.quant.smart_sync 控制：
  - full_sync_threshold_days: 距今超过该天数走 chenditc 全量
  - include_intraday: 同步当日时是否包含盘中未收盘数据
"""
import logging
from datetime import datetime
from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session
from app.models.stock_data_status import StockDataStatus
from app.schemas.quant import SyncDataRequest
from app.services.data.sync_progress import (
    init_progress, update_progress, finish_progress,
)

logger = logging.getLogger(__name__)


def _get_latest_date(provider_uri: str):
    """读取 qlib 日历末行作为 latest_date，返回 date 对象或 None。"""
    from pathlib import Path
    day_txt = Path(provider_uri) / "calendars" / "day.txt"
    if not day_txt.exists():
        return None
    lines = [line.strip() for line in day_txt.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return None
    try:
        return datetime.strptime(lines[-1], "%Y-%m-%d").date()
    except ValueError:
        return None


def predict_sync_path(provider_uri: str = None) -> dict:
    """预判同步路径（不执行同步，供前端展示用）。

    Returns:
        {"path": "chenditc_full"/"baostock_incremental"/"baostock_today",
         "latest_date": "YYYY-MM-DD" or None,
         "days_behind": int or None,
         "reason": "..."}
    """
    if provider_uri is None:
        provider_uri = settings.qlib_provider_path

    smart_cfg = settings.quant.get("smart_sync", {}) or {}
    threshold = smart_cfg.get("full_sync_threshold_days", 7)

    latest = _get_latest_date(provider_uri)
    if latest is None:
        return {"path": "chenditc_full", "latest_date": None, "days_behind": None,
                "reason": "qlib 数据不存在或日历为空，需 chenditc 全量初始化"}

    today = datetime.now().date()
    days_behind = (today - latest).days

    if days_behind > threshold:
        return {"path": "chenditc_full", "latest_date": latest.isoformat(),
                "days_behind": days_behind,
                "reason": f"latest_date 距今 {days_behind} 天 > {threshold}，走 chenditc 全量"}
    if days_behind >= 1:
        return {"path": "baostock_incremental", "latest_date": latest.isoformat(),
                "days_behind": days_behind,
                "reason": f"latest_date 距今 {days_behind} 天，走 baostock 增量补缺失日期"}
    # days_behind == 0，latest_date 是今天
    return {"path": "baostock_today", "latest_date": latest.isoformat(),
            "days_behind": days_behind,
            "reason": "latest_date 是今天，走 baostock 同步当日(含盘中)"}


async def smart_sync(universe: str = "csi300", include_intraday: bool = True) -> dict:
    """智能同步：按 latest_date 距今天数自动选择路径。

    Args:
        universe: 股票池（csi300/csi500/all）
        include_intraday: 是否包含盘中未收盘数据（默认 True，智能同步场景）。
            注意：incremental_sync_eod 的默认值是 False（保守，直接调用时盘中排除当日），
            智能同步路径会显式传 True 以支持"同步当日含盘中"需求。

    Returns:
        dict: 含 path/latest_date_before/latest_date_after/等同步摘要
    Raises:
        Exception: 同步失败时由 mark_sync_failed 标记后重新抛出
    """
    from app.services.data.sync_runner import _sync_via_chenditc, collect_qlib_stats, mark_sync_failed
    from app.services.data.eod_incremental import incremental_sync_eod

    provider_uri = settings.qlib_provider_path
    smart_cfg = settings.quant.get("smart_sync", {}) or {}
    # config.quant.smart_sync.include_intraday 可覆盖默认 True
    include_intraday = smart_cfg.get("include_intraday", include_intraday)

    # 预判路径
    prediction = predict_sync_path(provider_uri)
    path = prediction["path"]
    latest_before = prediction["latest_date"]
    logger.info("智能同步启动 universe=%s path=%s latest_before=%s include_intraday=%s",
                universe, path, latest_before, include_intraday)
    # 进度跟踪：chenditc 路径由 _sync_via_chenditc 内部 init_progress 全权负责，
    # 此处仅对 baostock 路径 init（避免双重 init 互相覆盖）
    if path != "chenditc_full":
        init_progress(universe, path, total_mb=0)
        update_progress(pct=0, status="running", message=f"智能同步启动：{path}")

    # 标记 syncing 状态
    async with async_session() as session:
        existing = await session.execute(
            select(StockDataStatus).where(StockDataStatus.universe == universe)
        )
        rec = existing.scalar_one_or_none()
        if rec is None:
            rec = StockDataStatus(universe=universe, status="syncing")
            session.add(rec)
        else:
            rec.status = "syncing"
            rec.last_error = None
        rec.last_updated = datetime.now()
        await session.commit()

    try:
        if path == "chenditc_full":
            # chenditc 全量初始化：进度由 _sync_via_chenditc 内部 init/update/finish 全权负责
            summary = await _sync_via_chenditc(universe)
            summary["path"] = "chenditc_full"
        elif path == "baostock_incremental":
            # baostock 增量补缺失日期（1-7天），含当日
            # 若当日已在日历（chenditc 全量包已含今天），需 overwrite=True 刷新当日为最新价
            days = prediction["days_behind"]
            today_in_cal = prediction["latest_date"] == datetime.now().strftime("%Y-%m-%d")
            incr_overwrite = True if today_in_cal else False
            update_progress(pct=10, status="running", message=f"baostock 增量同步最近 {days} 天...")
            summary = await incremental_sync_eod(
                universe=universe, days=days, source="baostock",
                include_intraday=include_intraday, overwrite=incr_overwrite,
            )
            summary["path"] = "baostock_incremental"
        else:  # baostock_today
            # 同步当日（latest_date 是今天）：当日已在日历中（chenditc 全量包含今天），
            # 必须 overwrite=True 覆盖刷新当日 bin（盘中价→收盘价，或刷新为最新价）
            update_progress(pct=10, status="running", message="baostock 同步当日数据...")
            summary = await incremental_sync_eod(
                universe=universe, days=1, source="baostock",
                include_intraday=include_intraday, overwrite=True,
            )
            summary["path"] = "baostock_today"

        # 更新状态为 ok
        stats = collect_qlib_stats(provider_uri)
        async with async_session() as session:
            existing = await session.execute(
                select(StockDataStatus).where(StockDataStatus.universe == universe)
            )
            rec = existing.scalar_one_or_none()
            if rec is None:
                rec = StockDataStatus(universe=universe)
                session.add(rec)
            new_latest = stats.get("latest_date") or summary.get("latest_date")
            if new_latest:
                rec.latest_date = new_latest
            rec.stock_count = stats.get("stock_count", summary.get("stock_count", 0))
            rec.row_count = stats.get("row_count", summary.get("row_count", 0))
            rec.status = "ok"
            rec.last_error = None
            rec.qlib_dir = provider_uri
            rec.last_sync_path = path
            rec.last_updated = datetime.now()
            await session.commit()

        # chenditc 路径的 finish_progress 由 _sync_via_chenditc 内部完成，此处仅对 baostock 路径收尾
        if path != "chenditc_full":
            update_progress(pct=95, status="verifying", message="校验数据完整性...")
        summary["latest_date_before"] = latest_before
        summary["latest_date_after"] = stats.get("latest_date")
        summary["universe"] = universe
        if path != "chenditc_full":
            finish_progress(True)
            update_progress(pct=100, status="done", message="智能同步完成")
        logger.info("智能同步完成 path=%s latest_after=%s",
                    path, stats.get("latest_date"))
        return summary

    except Exception as e:
        # chenditc 路径的 finish_progress(False) 由 _sync_via_chenditc 内部完成
        if path != "chenditc_full":
            finish_progress(False, str(e))
        await mark_sync_failed(universe, str(e))
        logger.exception("智能同步失败 path=%s", path)
        raise


async def run_smart_sync_task(req: SyncDataRequest):
    """后台执行智能同步（独立 session，供 BackgroundTasks 调用）。

    签名与 sync_runner.run_sync_task 兼容，便于 API 层直接替换。
    """
    universe = req.universe or settings.quant.get("universe", "csi300")
    await smart_sync(universe=universe)
