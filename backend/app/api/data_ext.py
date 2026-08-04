"""数据管理扩展 API：同步进度、数据预览、同步历史、数据源切换"""
import logging
import os
import re
from datetime import datetime
from fastapi import APIRouter, Query, Depends
from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_db
from app.core.errors import AppError
from app.models.sync_history import SyncHistory
from app.schemas.common import ApiResponse
from app.schemas.quant import RepairRequest
from app.services.data.sync_progress import get_progress, sync_is_active

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quant/data", tags=["data-ext"])
_stock_catalog_cache: list[dict] | None = None
_stock_catalog_updated_at: datetime | None = None


def _read_eod_result() -> dict | None:
    """从共享结果文件读取最近一次 EOD 同步的真实结果。

    结果由独立 worker 子进程写 data/eod_last_result.json，
    web 进程（含 reload 后）都能读到，不再依赖进程内存。
    """
    path = os.path.join(str(settings.PROJECT_ROOT / "data"), "eod_last_result.json")
    try:
        import json
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _normalize_search_text(text: str) -> str:
    return re.sub(r"\s+", "", (text or "")).lower()


def _get_name_keys(name: str) -> tuple[str, str]:
    try:
        from pypinyin import Style, lazy_pinyin

        initials = "".join(lazy_pinyin(name, style=Style.FIRST_LETTER))
        pinyin = "".join(lazy_pinyin(name))
        return initials.lower(), pinyin.lower()
    except Exception:
        return "", ""


async def _get_stock_catalog() -> list[dict]:
    global _stock_catalog_cache, _stock_catalog_updated_at
    if _stock_catalog_cache and _stock_catalog_updated_at and (datetime.now() - _stock_catalog_updated_at).seconds < 3600:
        return _stock_catalog_cache

    from app.services.quant.data_adapter import get_stock_list

    items = await get_stock_list()
    _stock_catalog_cache = items
    _stock_catalog_updated_at = datetime.now()
    return items


@router.get("/sync-progress")
async def sync_progress_api():
    """获取当前同步进度（修复2: 同步进度 API 推送）"""
    progress = get_progress()
    return ApiResponse(ok=True, data=progress)


@router.get("/preview")
async def data_preview_api(
    code: str = Query(None, description="股票代码，如 SH600000；为空时默认预览沪深300"),
    limit: int = Query(30, le=100, description="返回天数"),
):
    """数据预览：返回指定股票最近 N 天的 OHLCV 数据（添加3: 数据预览）"""
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)

    if not code:
        code = "csi300"
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

        fields = ["$open", "$close", "$high", "$low", "$volume"]
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
            })
        return rows

    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _load)
    return ApiResponse(ok=True, data={"items": data, "code": code.upper(), "count": len(data)})


@router.get("/stocks/search")
async def search_stocks_api(
    q: str = Query(..., min_length=1, description="股票名称 / 首字母 / 代码"),
    limit: int = Query(20, ge=1, le=50, description="返回条数上限"),
):
    """搜索 A 股：支持中文名称、拼音首字母和股票代码。"""
    q_norm = _normalize_search_text(q)
    if not q_norm:
        return ApiResponse(ok=True, data={"items": [], "query": q, "count": 0})

    catalog = await _get_stock_catalog()
    matches: list[dict] = []
    for item in catalog:
        code = str(item.get("code", "")).strip().lower()
        name = str(item.get("name", "")).strip()
        initials, pinyin = _get_name_keys(name)
        name_norm = _normalize_search_text(name)

        score = -1
        if q_norm == code:
            score = 1000
        elif q_norm == name_norm:
            score = 950
        elif q_norm == initials:
            score = 900
        elif code.startswith(q_norm):
            score = 850
        elif name_norm.startswith(q_norm):
            score = 800
        elif initials.startswith(q_norm):
            score = 760
        elif q_norm in code:
            score = 700
        elif q_norm in name_norm:
            score = 650
        elif q_norm in initials:
            score = 600
        elif q_norm in pinyin:
            score = 550

        if score < 0:
            continue

        matches.append({
            "code": item.get("code"),
            "name": name,
            "qlib_code": item.get("qlib_code"),
            "initials": initials,
            "pinyin": pinyin,
            "score": score,
        })

    matches.sort(key=lambda x: (-x["score"], x["code"]))
    return ApiResponse(ok=True, data={"items": matches[:limit], "query": q, "count": len(matches)})


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


@router.get("/sync-stats")
async def sync_stats_api(
    days: int = Query(30, ge=1, le=365, description="统计最近 N 天"),
    universe: str = Query(None, description="按股票池筛选"),
    db=Depends(get_db),
):
    """同步统计聚合：路径分布/成功率/耗时/失败原因/完整性趋势。

    供前端 SyncMonitor 面板展示用。
    """
    from datetime import timedelta

    cutoff = datetime.now() - timedelta(days=days)

    # 基础查询
    query = select(SyncHistory).where(SyncHistory.started_at >= cutoff)
    if universe:
        query = query.where(SyncHistory.universe == universe)
    query = query.order_by(SyncHistory.started_at.desc())
    result = await db.execute(query)
    rows = result.scalars().all()

    if not rows:
        return ApiResponse(ok=True, data={
            "path_distribution": {},
            "success_rate": {"ok": 0, "failed": 0, "running": 0, "total": 0, "rate": 0},
            "duration_stats": {"avg": 0, "max": 0, "min": 0, "p50": 0, "p95": 0},
            "daily_duration": [],
            "top5_slowest": [],
            "failure_reasons": [],
            "integrity_trend": [],
        })

    # 1. 路径分布
    path_dist = {}
    for r in rows:
        p = r.sync_path or r.data_source or "unknown"
        path_dist[p] = path_dist.get(p, 0) + 1

    # 2. 成功率
    status_counts = {"ok": 0, "failed": 0, "running": 0}
    for r in rows:
        s = r.status or "running"
        status_counts[s] = status_counts.get(s, 0) + 1
    total = len(rows)
    rate = status_counts["ok"] / total if total > 0 else 0

    # 3. 耗时统计（仅 status=ok/failed 的已完成记录）
    durations = [r.duration_seconds for r in rows if r.duration_seconds is not None]
    if durations:
        durations_sorted = sorted(durations)
        n = len(durations_sorted)
        avg = sum(durations) / n
        p50 = durations_sorted[n // 2]
        p95 = durations_sorted[int(n * 0.95)] if n >= 20 else durations_sorted[-1]
        duration_stats = {
            "avg": round(avg, 1),
            "max": round(max(durations), 1),
            "min": round(min(durations), 1),
            "p50": round(p50, 1),
            "p95": round(p95, 1),
        }
    else:
        duration_stats = {"avg": 0, "max": 0, "min": 0, "p50": 0, "p95": 0}

    # 4. 每日耗时列表（按日期正序，供折线图）
    daily_map = {}
    for r in rows:
        if r.started_at and r.duration_seconds is not None:
            date_str = r.started_at.strftime("%Y-%m-%d")
            daily_map.setdefault(date_str, []).append(r)
    daily_duration = []
    for date_str in sorted(daily_map.keys()):
        recs = daily_map[date_str]
        avg_dur = sum(r.duration_seconds for r in recs) / len(recs)
        last = recs[0]  # 最近一条
        daily_duration.append({
            "date": date_str,
            "duration": round(avg_dur, 1),
            "path": last.sync_path or last.data_source or "unknown",
            "status": last.status,
        })

    # 5. Top5 最耗时
    by_duration = sorted(
        [r for r in rows if r.duration_seconds is not None],
        key=lambda x: x.duration_seconds, reverse=True
    )[:5]
    top5_slowest = [{
        "id": r.id,
        "started_at": r.started_at.strftime("%Y-%m-%d %H:%M") if r.started_at else None,
        "duration": round(r.duration_seconds, 1),
        "path": r.sync_path or r.data_source or "unknown",
        "status": r.status,
        "universe": r.universe,
    } for r in by_duration]

    # 6. 失败原因分类
    failure_reasons = []
    failed_rows = [r for r in rows if r.status == "failed" and r.error]
    reason_map = {}
    for r in failed_rows:
        err = (r.error or "")[:200]
        # 简单关键词分类
        if "timeout" in err.lower() or "超时" in err:
            reason = "超时"
        elif "connection" in err.lower() or "连接" in err or "network" in err.lower():
            reason = "网络/连接"
        elif "login" in err.lower() or "auth" in err.lower() or "认证" in err:
            reason = "认证失败"
        elif "data" in err.lower() and "not" in err.lower():
            reason = "数据缺失"
        else:
            reason = "其他"
        reason_map[reason] = reason_map.get(reason, 0) + 1
    failure_reasons = [{"reason": k, "count": v} for k, v in
                        sorted(reason_map.items(), key=lambda x: x[1], reverse=True)]

    # 7. 数据完整性趋势（stock_count/latest_date 按日期正序）
    integrity_rows = [r for r in rows if r.stock_count is not None or r.latest_date]
    integrity_rows.sort(key=lambda x: x.started_at or datetime.min)
    # 去重：同一日期只保留最后一条
    seen_dates = set()
    integrity_trend = []
    for r in reversed(integrity_rows):  # 逆序，保留每天最新
        date_str = r.started_at.strftime("%Y-%m-%d") if r.started_at else None
        if date_str and date_str not in seen_dates:
            seen_dates.add(date_str)
            integrity_trend.append({
                "date": date_str,
                "stock_count": r.stock_count,
                "latest_date": r.latest_date,
                "row_count": r.row_count,
            })
    integrity_trend.reverse()  # 恢复正序

    return ApiResponse(ok=True, data={
        "path_distribution": path_dist,
        "success_rate": {**status_counts, "total": total, "rate": round(rate, 4)},
        "duration_stats": duration_stats,
        "daily_duration": daily_duration,
        "top5_slowest": top5_slowest,
        "failure_reasons": failure_reasons,
        "integrity_trend": integrity_trend,
        "failed_records": [{
            "id": r.id,
            "started_at": r.started_at.strftime("%Y-%m-%d %H:%M") if r.started_at else None,
            "error": (r.error or "")[:500],
            "path": r.sync_path or r.data_source or "unknown",
            "universe": r.universe,
        } for r in failed_rows[:20]],  # 最近20条失败记录
    })


@router.post("/eod-sync")
async def eod_sync_api(
    universe: str = Query("csi300", description="股票池: csi300/csi500/all"),
    days: int = Query(5, ge=1, le=30, description="同步最近N天数据"),
    overwrite: bool = Query(False, description="是否覆盖已有日期数据"),
):
    """增量同步EOD数据（基于akshare国内源，拉取最近N天日K数据）- 独立 worker 后台执行

    与 baostock 全量同步互补：akshare 国内源访问快，
    适合日常增量更新最近几个交易日的 OHLCV 数据。

    同步在独立 worker 子进程（.venv/bin/python -m app.services.data.sync_worker）
    中运行，与 web 进程解耦：uvicorn --reload 重启不会等它、也不会误杀它。
    结果写 data/eod_last_result.json，前端通过 /quant/data/eod-result 轮询。
    """
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)

    if sync_is_active():
        return ApiResponse(ok=False, error={
            "code": "SYNC_IN_PROGRESS",
            "message": "正在同步/修复中，请稍候（存在活跃同步任务）",
            "status": 409,
        })

    from app.services.data.sync_worker import spawn_sync_worker
    spawn_sync_worker("eod", universe, days=days, overwrite=overwrite)
    return ApiResponse(ok=True, data={
        "message": f"EOD增量同步已提交（universe={universe}, days={days}），独立进程后台执行中",
        "universe": universe,
        "days": days,
        "overwrite": overwrite,
    })


@router.get("/eod-result")
async def eod_result_api():
    """获取最近一次 EOD 增量同步的真实结果（独立 worker 完成后写入文件）。

    EOD 同步在独立 worker 子进程执行，提交接口立即返回，无法携带实际结果；
    前端通过本接口（读取 data/eod_last_result.json）轮询拿到 success/total_stocks/
    new_dates 等真实数据。
    """
    return ApiResponse(ok=True, data=_read_eod_result())


@router.post("/sync-indices")
async def sync_indices_api():
    """同步指数数据到 qlib bin（独立 worker 后台执行）

    通过 akshare 拉取主要指数（上证、沪深300、中证500等）的日K行情，
    写入 qlib bin 格式。如遇日历中不存在的新日期会自动扩展日历。
    """
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)

    if sync_is_active():
        return ApiResponse(ok=False, error={
            "code": "SYNC_IN_PROGRESS",
            "message": "正在同步/修复中，请稍候（存在活跃同步任务）",
            "status": 409,
        })

    from app.services.data.sync_worker import spawn_sync_worker
    spawn_sync_worker("indices", "indices")
    return ApiResponse(ok=True, data={"message": "指数同步已提交，独立进程后台执行中"})


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


@router.get("/validate")
async def validate_api(universe: str = Query("all")):
    """全市场数据校验：bin 字段完整性 + DB/qlib 字段与覆盖一致性 + 日历同步。

    返回结构化报告（checks/calendar/drift），前端据此展示差异并决定是否修复。
    """
    from app.services.data.validation import run_validation

    report = await run_validation(provider_uri=settings.qlib_provider_path, universe=universe)
    return ApiResponse(ok=True, data=report)


@router.post("/repair")
async def repair_api(
    req: RepairRequest,
    db=Depends(get_db),
):
    """一键补齐：按校验差异修复 DB 与 qlib 不一致（独立 worker 后台执行）。

    前 3 步（day.txt / bin / instruments）从 PG 重建，不消耗 baostock 配额；
    仅当 include_baostock=true 且 PG 缺失交易日时，才从 baostock 增量补拉。

    同步在独立 worker 子进程（app.services.data.sync_worker --kind repair）中运行，
    与 web 进程解耦，uvicorn --reload 重启不会等它。
    """
    from app.models.stock_data_status import StockDataStatus

    universe = req.universe or settings.quant.get("universe", "csi300")
    if sync_is_active():
        active = get_progress()
        return ApiResponse(ok=False, error={
            "code": "SYNC_IN_PROGRESS",
            "message": f"正在同步/修复中，请稍候（当前 universe={active.get('universe') if active else '?'}）",
            "status": 409,
        })

    existing = await db.execute(
        select(StockDataStatus).where(StockDataStatus.universe == universe)
    )
    rec = existing.scalar_one_or_none()
    if rec is None:
        rec = StockDataStatus(universe=universe, status="syncing")
        db.add(rec)
    else:
        rec.status = "syncing"
        rec.last_error = None
    rec.sync_trigger = "manual"
    rec.last_updated = datetime.now()
    await db.commit()

    from app.services.data.sync_worker import spawn_sync_worker
    spawn_sync_worker("repair", universe, include_baostock=req.include_baostock)
    return ApiResponse(ok=True, data={
        "message": f"已触发数据补齐（universe={universe}"
                   + ("，含 baostock 增量" if req.include_baostock else "，仅从 PG 重建") + "）",
        "universe": universe,
        "include_baostock": req.include_baostock,
    })


@router.post("/sync-industry")
async def sync_industry_api():
    """同步申万行业分类数据（已禁用，后期规划）

    行业同步功能暂未开放，后期规划。代码保留在 industry_sync.py 供未来复用。
    """
    return ApiResponse(ok=False, error={
        "code": "INDUSTRY_SYNC_DISABLED",
        "message": "行业同步暂未开放，后期规划",
        "status": 503,
    })
