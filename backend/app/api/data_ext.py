"""数据管理扩展 API：同步进度、数据预览、同步历史、数据源切换"""
import logging
import csv
import io
import os
from datetime import datetime
from fastapi import APIRouter, Query, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func

from app.core.config import settings
from app.core.database import get_db, async_session
from app.core.errors import AppError
from app.models.sync_history import SyncHistory
from app.models.stock_data_status import StockDataStatus
from app.schemas.common import ApiResponse
from app.services.data.sync_progress import get_progress
from app.services.data.eod_incremental import incremental_sync_eod

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quant/data", tags=["data-ext"])


@router.get("/sync-progress")
async def sync_progress_api():
    """获取当前同步进度（修复2: 同步进度 API 推送）"""
    progress = get_progress()
    return ApiResponse(ok=True, data=progress)


@router.get("/preview")
async def data_preview_api(
    code: str = Query(..., description="股票代码，如 SH600000"),
    limit: int = Query(30, le=100, description="返回天数"),
):
    """数据预览：返回指定股票最近 N 天的 OHLCV 数据（添加3: 数据预览）"""
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)

    import asyncio
    from app.services.quant.qlib_init import init_qlib

    def _load():
        init_qlib()
        from qlib.data import D
        code_upper = code.upper()

        # 检查是否是股票池名（csi300/csi500/all 等）
        provider_uri = settings.qlib_provider_path
        pool_file = os.path.join(provider_uri, "instruments", f"{code.lower()}.txt")
        if os.path.exists(pool_file):
            # 解析股票池文件，取最新成分股（end_date 最大的一批）
            entries = []
            with open(pool_file, "r", encoding="utf-8") as pf:
                for line in pf:
                    parts = line.strip().split("\t")
                    if len(parts) >= 3:
                        entries.append((parts[0], parts[2]))
                    elif parts and parts[0]:
                        entries.append((parts[0], ""))
            if entries:
                entries.sort(key=lambda x: x[1], reverse=True)
                seen = set()
                instruments = []
                for c, _ in entries:
                    if c not in seen:
                        seen.add(c)
                        instruments.append(c)
                    if len(instruments) >= 5:
                        break
            else:
                instruments = [code_upper]
        else:
            instruments = [code_upper]

        fields = ["$open", "$close", "$high", "$low", "$volume", "$factor"]
        try:
            df = D.features(instruments, fields, start_time="2020-01-01",
                            end_time=datetime.now().strftime("%Y-%m-%d"), freq="day")
        except Exception:
            df = None
        if df is None or df.empty:
            return []
        df = df.sort_index(ascending=False).head(limit)
        rows = []
        for (inst, dt), row in df.iterrows():
            # 处理 dt 可能是 str 或 datetime 的情况
            if hasattr(dt, "date"):
                date_str = str(dt.date())
            elif hasattr(dt, "strftime"):
                date_str = dt.strftime("%Y-%m-%d")
            else:
                date_str = str(dt)[:10]
            rows.append({
                "date": date_str,
                "code": inst,
                "open": round(float(row.get("$open", 0)), 2),
                "close": round(float(row.get("$close", 0)), 2),
                "high": round(float(row.get("$high", 0)), 2),
                "low": round(float(row.get("$low", 0)), 2),
                "volume": int(row.get("$volume", 0)),
                "factor": round(float(row.get("$factor", 1)), 4),
            })
        return rows

    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _load)
    return ApiResponse(ok=True, data={"items": data, "code": code.upper(), "count": len(data)})


@router.get("/sync-history")
async def sync_history_api(
    limit: int = Query(50, le=200),
    db=Depends(get_db),
):
    """同步历史记录（添加4: 同步历史记录）"""
    result = await db.execute(
        select(SyncHistory).order_by(SyncHistory.started_at.desc()).limit(limit)
    )
    rows = result.scalars().all()
    items = [{
        "id": r.id,
        "universe": r.universe,
        "data_source": r.data_source,
        "status": r.status,
        "started_at": r.started_at.strftime("%Y-%m-%dT%H:%M:%S+08:00") if r.started_at else None,
        "finished_at": r.finished_at.strftime("%Y-%m-%dT%H:%M:%S+08:00") if r.finished_at else None,
        "duration_seconds": r.duration_seconds,
        "version": r.version,
        "latest_date": r.latest_date,
        "stock_count": r.stock_count,
        "row_count": r.row_count,
        "file_size_mb": r.file_size_mb,
        "error": r.error,
    } for r in rows]
    return ApiResponse(ok=True, data={"items": items, "total": len(items)})


@router.put("/data-source")
async def switch_data_source_api(
    source: str = Query(..., description="chenditc 或 akshare"),
):
    """切换数据源（添加2: 多数据源切换）"""
    if source not in ("chenditc", "akshare"):
        raise AppError("VALIDATION_ERROR", "数据源仅支持 chenditc 或 akshare", 422)

    # 更新配置文件
    import yaml
    config_path = settings.PROJECT_ROOT / "config.yaml"
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        cfg.setdefault("quant", {})["data_source"] = source
        with open(config_path, "w") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
        # 更新运行时配置
        settings.quant["data_source"] = source
        return ApiResponse(ok=True, data={"data_source": source, "message": f"数据源已切换为 {source}"})
    except Exception as e:
        raise AppError("CONFIG_ERROR", f"配置更新失败: {e}", 500)


@router.get("/data-source")
async def get_data_source_api():
    """获取当前数据源"""
    return ApiResponse(ok=True, data={"source": settings.quant.get("data_source", "chenditc")})


@router.post("/incremental-sync")
async def incremental_sync_api(background_tasks=None):
    """增量数据同步（添加1: 数据增量更新）"""
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)

    # 增量同步在后台执行
    from app.api.quant_data import _run_sync_task
    from app.schemas.quant import SyncDataRequest
    req = SyncDataRequest(universe=settings.quant.get("universe", "csi300"))
    background_tasks.add_task(_run_sync_task, req)
    return ApiResponse(ok=True, data={"message": "增量同步已提交"})


@router.post("/eod-sync")
async def eod_sync_api(
    background_tasks: BackgroundTasks,
    universe: str = Query("csi300", description="股票池: csi300/csi500/all"),
    days: int = Query(5, ge=1, le=30, description="同步最近N天数据"),
    overwrite: bool = Query(False, description="是否覆盖已有日期数据"),
):
    """增量同步EOD数据（基于akshare国内源，拉取最近N天日K数据）- 后台执行

    与 chenditc 全量同步互补：akshare 国内源访问快，
    适合日常增量更新最近几个交易日的 OHLCV 数据。

    立即返回提交确认，同步在后台执行。可通过 /quant/data/sync-progress 查询进度。
    """
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)

    background_tasks.add_task(_run_eod_sync, universe, days, overwrite)
    return ApiResponse(ok=True, data={
        "message": f"EOD增量同步已提交（universe={universe}, days={days}），后台执行中",
        "universe": universe,
        "days": days,
        "overwrite": overwrite,
    })


async def _run_eod_sync(universe: str, days: int, overwrite: bool):
    """后台执行EOD增量同步"""
    try:
        result = await incremental_sync_eod(
            universe=universe, days=days, overwrite=overwrite,
        )
        if result.get("ok"):
            logger.info("EOD同步完成: %s", result)
        else:
            logger.error("EOD同步返回错误: %s", result)
    except Exception as e:
        logger.error("EOD同步失败: %s", e)


@router.post("/sync-indices")
async def sync_indices_api(background_tasks: BackgroundTasks):
    """同步指数数据到 qlib bin（后台执行）

    通过 akshare 拉取主要指数（上证、沪深300、中证500等）的日K行情，
    写入 qlib bin 格式。如遇日历中不存在的新日期会自动扩展日历。
    """
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)

    async def _run():
        try:
            from app.services.data.index_sync import sync_indices_to_qlib
            result = sync_indices_to_qlib(settings.qlib_provider_path, days=365)
            if result.get("ok"):
                logger.info("指数同步完成: %s", result)
            else:
                logger.error("指数同步返回错误: %s", result)
        except Exception as e:
            logger.error("指数同步失败: %s", e)

    background_tasks.add_task(_run)
    return ApiResponse(ok=True, data={"message": "指数同步已提交，后台执行中"})


@router.get("/integrity-check")
async def integrity_check_api(universe: str = Query(None)):
    # 数据完整性校验：检测每只股票的 bin 文件长度是否与日历天数一致
    from app.services.data.integrity_check import check_integrity
    import asyncio
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, check_integrity, settings.qlib_provider_path, universe
    )
    return ApiResponse(ok=True, data=result)
