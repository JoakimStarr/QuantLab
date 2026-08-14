"""定时数据管理同步：配置读写 + 调度 tick。

与 sync_schedule_service（政策风向）平行，但针对数据管理页：
- 环节：一键全同步 / 增量 EOD / 指数 / ETF / 财报（可多选）
- 每环节独立 spawn worker（kind=full/eod/indices/etf/fundamental）
- 命中规则一致：启用 + 到点 + 工作日(可选) + 当日未触发

注意：数据管理环节写 qlib bin，多个 bin 写 worker 并发会互相覆盖日历。
full 本身内部串行；多环节同时勾选时按列表顺序串行 spawn（子进程各自抢锁，
worker 内部有 SyncLock，抢不到直接退出，因此并发也是安全的）。
"""
import logging
from datetime import date, datetime, time

from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session
from app.models.data_sync_schedule import DataSyncSchedule

logger = logging.getLogger(__name__)

SCHEDULE_ID = 1


def _defaults() -> dict:
    return {
        "enabled": False,
        "run_time": settings.scheduler.quant_data_update_time or "18:00",
        "workdays_only": True,
        "include_full": True,
        "include_eod": False,
        "include_indices": False,
        "include_etf": False,
        "include_fundamental": False,
        "years": 5,
        "universe": "all",
        "eod_days": 5,
        "etf_days": 30,
    }


def _to_dict(row: DataSyncSchedule) -> dict:
    return {
        "enabled": row.enabled,
        "run_time": row.run_time,
        "workdays_only": row.workdays_only,
        "include_full": row.include_full,
        "include_eod": row.include_eod,
        "include_indices": row.include_indices,
        "include_etf": row.include_etf,
        "include_fundamental": row.include_fundamental,
        "years": row.years,
        "universe": row.universe,
        "eod_days": row.eod_days,
        "etf_days": row.etf_days,
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
    """读取定时同步配置；无记录时返回默认值（不落库）。"""
    async with async_session() as session:
        row = (await session.execute(
            select(DataSyncSchedule).where(DataSyncSchedule.id == SCHEDULE_ID)
        )).scalar_one_or_none()
    if row is None:
        return _defaults()
    return _to_dict(row)


async def save_schedule(cfg: dict) -> dict:
    """保存定时同步配置（单行 upsert），返回落库后的配置。"""
    data = _defaults()
    for key in ("enabled", "workdays_only", "include_full", "include_eod",
                "include_indices", "include_etf", "include_fundamental"):
        if key in cfg and isinstance(cfg[key], bool):
            data[key] = cfg[key]
    if "run_time" in cfg:
        data["run_time"] = _validate_run_time(str(cfg["run_time"]))
    for key in ("years", "eod_days", "etf_days"):
        if key in cfg and isinstance(cfg[key], int):
            data[key] = max(1, cfg[key])
    if "universe" in cfg and isinstance(cfg["universe"], str) and cfg["universe"].strip():
        data["universe"] = cfg["universe"].strip()

    async with async_session() as session:
        row = (await session.execute(
            select(DataSyncSchedule).where(DataSyncSchedule.id == SCHEDULE_ID)
        )).scalar_one_or_none()
        if row is None:
            row = DataSyncSchedule(id=SCHEDULE_ID)
            session.add(row)
        for key, value in data.items():
            setattr(row, key, value)
        await session.commit()
        await session.refresh(row)
        return _to_dict(row)


def _is_workday(d: date) -> bool:
    """周一~周五为工作日。"""
    return d.weekday() < 5


async def tick_scheduled_data_sync() -> None:
    """每分钟调度入口：命中触发窗口则按配置 spawn 各同步环节（幂等防重）。"""
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
            select(DataSyncSchedule).where(DataSyncSchedule.id == SCHEDULE_ID)
        )).scalar_one_or_none()
        if row is None:
            row = DataSyncSchedule(id=SCHEDULE_ID)
            session.add(row)
        row.last_run_date = day
        await session.commit()


async def _run_workers(cfg: dict) -> None:
    """按配置 spawn 各同步环节 worker（独立子进程）。

    full 已内部串行（回填→指数→ETF→宏观→财报→外盘），且写 bin；
    其余环节按顺序 spawn，worker 各自抢 SyncLock，抢不到自动退出，因此不会并发写 bin。
    """
    from app.services.data.sync_worker import spawn_sync_worker

    spawned: list[str] = []
    if cfg["include_full"]:
        spawn_sync_worker("full", cfg["universe"], years=cfg["years"])
        spawned.append(f"一键全同步({cfg['years']}年)")
    if cfg["include_eod"]:
        spawn_sync_worker("eod", cfg["universe"], days=cfg["eod_days"])
        spawned.append(f"EOD 行情({cfg['universe']},{cfg['eod_days']}天)")
    if cfg["include_indices"]:
        spawn_sync_worker("indices", "indices")
        spawned.append("指数同步")
    if cfg["include_etf"]:
        spawn_sync_worker("etf", "all", days=cfg["etf_days"])
        spawned.append(f"ETF 增量({cfg['etf_days']}天)")
    if cfg["include_fundamental"]:
        spawn_sync_worker("fundamental", "all")
        spawned.append("财报同步")
    logger.info("定时数据管理同步触发: %s", "、".join(spawned) if spawned else "(无环节启用)")
