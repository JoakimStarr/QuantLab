"""定时数据刷新：配置读写 + 调度 tick。

调度策略：注册一个每分钟 tick（update_service.register_scheduled_jobs），
每次 tick 读单行配置（SyncSchedule id=1），命中条件后 spawn 对应 worker：
- 已启用
- 当前时刻 ≥ run_time（HH:MM），且当日尚未触发（last_run_date 幂等，不重复触发）
- workdays_only=True 时仅工作日触发

为什么不直接用 APScheduler cron：
- 用户改时间/开关要动态反映，cron 需要重新注册；
- 每分钟 tick + 配置比对天然支持"当天已跑过不重跑"的幂等语义。
"""
import logging
from datetime import date, datetime, time

from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session
from app.models.sync_schedule import SyncSchedule

logger = logging.getLogger(__name__)

SCHEDULE_ID = 1


def _defaults() -> dict:
    """默认配置：fallback 到 config.yaml 的 legacy quant_data_update_time。"""
    return {
        "enabled": False,
        "run_time": settings.scheduler.quant_data_update_time or "18:00",
        "workdays_only": True,
        "include_news": True,
        "include_ai": True,
        "include_market": True,
        "ai_backfill_days": 30,
        "market_days": 5,
        "market_universe": settings.quant.get("universe", "csi300"),
    }


def _to_dict(row: SyncSchedule) -> dict:
    return {
        "enabled": row.enabled,
        "run_time": row.run_time,
        "workdays_only": row.workdays_only,
        "include_news": row.include_news,
        "include_ai": row.include_ai,
        "include_market": row.include_market,
        "ai_backfill_days": row.ai_backfill_days,
        "market_days": row.market_days,
        "market_universe": row.market_universe,
        "last_run_date": row.last_run_date.isoformat() if row.last_run_date else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _validate_run_time(value: str) -> str:
    """校验 HH:MM 格式，统一为 HH:MM。"""
    if not value or ":" not in value:
        raise ValueError(f"run_time 格式非法: {value!r}，需要 HH:MM")
    parts = value.split(":")
    try:
        hour, minute = int(parts[0]), int(parts[1][:2])
    except (TypeError, ValueError):
        raise ValueError(f"run_time 格式非法: {value!r}，需要 HH:MM") from None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"run_time 超出范围: {value!r}，需要 00:00-23:59")
    return f"{hour:02d}:{minute:02d}"


async def get_schedule() -> dict:
    """读取定时刷新配置；无记录时返回默认值（不落库）。"""
    async with async_session() as session:
        row = (await session.execute(
            select(SyncSchedule).where(SyncSchedule.id == SCHEDULE_ID)
        )).scalar_one_or_none()
    if row is None:
        return _defaults()
    return _to_dict(row)


async def save_schedule(cfg: dict) -> dict:
    """保存定时刷新配置（单行 upsert），返回落库后的配置。"""
    data = _defaults()
    for key in ("enabled", "workdays_only", "include_news", "include_ai", "include_market"):
        if key in cfg and isinstance(cfg[key], bool):
            data[key] = cfg[key]
    if "run_time" in cfg:
        data["run_time"] = _validate_run_time(str(cfg["run_time"]))
    for key in ("ai_backfill_days", "market_days"):
        if key in cfg and isinstance(cfg[key], int):
            data[key] = max(1, cfg[key])
    if "market_universe" in cfg and isinstance(cfg["market_universe"], str) and cfg["market_universe"].strip():
        data["market_universe"] = cfg["market_universe"].strip()

    async with async_session() as session:
        row = (await session.execute(
            select(SyncSchedule).where(SyncSchedule.id == SCHEDULE_ID)
        )).scalar_one_or_none()
        if row is None:
            row = SyncSchedule(id=SCHEDULE_ID)
            session.add(row)
        for key, value in data.items():
            setattr(row, key, value)
        await session.commit()
        await session.refresh(row)
        return _to_dict(row)


def _is_workday(d: date) -> bool:
    """周一~周五为工作日。"""
    return d.weekday() < 5


async def tick_scheduled_sync() -> None:
    """每分钟调度入口：命中触发窗口则 spawn 配置好的同步环节（幂等防重）。"""
    cfg = await get_schedule()
    now = datetime.now()

    if not cfg["enabled"]:
        return
    if cfg["workdays_only"] and not _is_workday(now.date()):
        return
    if now.time() < time.fromisoformat(cfg["run_time"]):
        return
    if cfg.get("last_run_date") == now.date().isoformat():
        return

    await _run_workers(cfg)
    await _mark_last_run(now.date())


async def _mark_last_run(day: date) -> None:
    """记录当日已触发（last_run_date），防同日重复触发。"""
    async with async_session() as session:
        row = (await session.execute(
            select(SyncSchedule).where(SyncSchedule.id == SCHEDULE_ID)
        )).scalar_one_or_none()
        if row is None:
            row = SyncSchedule(id=SCHEDULE_ID)
            session.add(row)
        row.last_run_date = day
        await session.commit()


async def _run_workers(cfg: dict) -> None:
    """按配置 spawn 各同步环节 worker（独立子进程，不阻塞调度）。"""
    from app.services.data.sync_worker import spawn_sync_worker

    spawned: list[str] = []
    if cfg["include_news"]:
        spawn_sync_worker("policy", "policy")
        spawned.append("新闻联播")
    if cfg["include_ai"]:
        spawn_sync_worker("policy_ai", "policy_ai", days=cfg["ai_backfill_days"])
        spawned.append(f"AI 解读(回填{cfg['ai_backfill_days']}天)")
    if cfg["include_market"]:
        spawn_sync_worker("eod", cfg["market_universe"], days=cfg["market_days"])
        spawned.append(f"EOD 行情({cfg['market_universe']},{cfg['market_days']}天)")
    logger.info("定时数据刷新触发: %s", "、".join(spawned) if spawned else "(无环节启用)")
