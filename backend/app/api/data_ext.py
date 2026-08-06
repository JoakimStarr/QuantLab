"""数据管理扩展 API：同步进度、数据预览、同步历史、数据源切换"""
import logging
import math
import os
import re
from datetime import datetime
from fastapi import APIRouter, Query, Depends
from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_db
from app.core.errors import AppError
from app.models.stock_index import StockIndex
from app.models.sync_history import SyncHistory
from app.schemas.common import ApiResponse
from app.schemas.quant import RepairRequest
from app.services.data.sync_progress import busy_message, get_progress, writes_bins_active

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quant/data", tags=["data-ext"])
_stock_catalog_cache: list[dict] | None = None
_stock_catalog_updated_at: datetime | None = None


def _clean_num(v):
    """NaN/None/非数值 → None，其余转 float（回填进行中或停牌日 bin 会读到 NaN）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


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
            # 回填进行中或停牌日会读到 NaN，统一转 null，避免 int(NaN) 崩溃
            open_v, close_v = _clean_num(row.get("$open")), _clean_num(row.get("$close"))
            high_v, low_v = _clean_num(row.get("$high")), _clean_num(row.get("$low"))
            volume_v = _clean_num(row.get("$volume"))
            rows.append({
                "date": date_str,
                "code": inst,
                "open": round(open_v, 2) if open_v is not None else None,
                "close": round(close_v, 2) if close_v is not None else None,
                "high": round(high_v, 2) if high_v is not None else None,
                "low": round(low_v, 2) if low_v is not None else None,
                "volume": int(volume_v) if volume_v is not None else None,
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
    source: str = Query("baostock", description="数据源: baostock（主源，一次拉全市场）/ akshare（兜底，逐只爬）"),
):
    """增量同步EOD数据（拉取最近N天日K数据）- 独立 worker 后台执行

    与 baostock 全量同步互补：增量更新最近几个交易日的 OHLCV 数据。
    默认 baostock（一次拉全市场）；akshare 作为国内源兜底（逐只爬）。

    同步在独立 worker 子进程（.venv/bin/python -m app.services.data.sync_worker）
    中运行，与 web 进程解耦：uvicorn --reload 重启不会等它、也不会误杀它。
    结果写 data/eod_last_result.json，前端通过 /quant/data/eod-result 轮询。
    """
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)

    if writes_bins_active():
        return ApiResponse(ok=False, error={
            "code": "SYNC_IN_PROGRESS",
            "message": busy_message(),
            "status": 409,
        })

    if source not in ("baostock", "akshare"):
        raise AppError("VALIDATION_ERROR", "source 仅支持 baostock/akshare", 422)

    from app.services.data.sync_worker import spawn_sync_worker
    spawn_sync_worker("eod", universe, days=days, overwrite=overwrite, source=source)
    return ApiResponse(ok=True, data={
        "message": f"EOD增量同步已提交（universe={universe}, days={days}, source={source}），独立进程后台执行中",
        "universe": universe,
        "days": days,
        "overwrite": overwrite,
        "source": source,
    })


@router.get("/eod-result")
async def eod_result_api():
    """获取最近一次 EOD 增量同步的真实结果（独立 worker 完成后写入文件）。

    EOD 同步在独立 worker 子进程执行，提交接口立即返回，无法携带实际结果；
    前端通过本接口（读取 data/eod_last_result.json）轮询拿到 success/total_stocks/
    new_dates 等真实数据。
    """
    return ApiResponse(ok=True, data=_read_eod_result())


@router.post("/sync-full")
async def sync_full_api(
    years: int = Query(5, ge=0, le=30, description="A股回填年数（0=仅增量补最新）"),
    universe: str = Query("all", description="股票池（仅状态标签，回填本质是全市场）"),
):
    """一键全同步：按依赖顺序串联 A股回填 → 指数 → 宏观 → 财报 → 外盘（独立 worker 后台执行）。

    顺序约束：bin 必须对齐最终日历 day.txt。A股回填先确立日历，指数/宏观/财报/外盘
    再按最终日历广播写 bin，否则 qlib 读位错位、因子全 NaN。
    """
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)

    if writes_bins_active():
        return ApiResponse(ok=False, error={
            "code": "SYNC_IN_PROGRESS",
            "message": busy_message("full"),
            "status": 409,
        })

    from app.services.data.sync_worker import spawn_sync_worker
    spawn_sync_worker("full", universe, years=years)
    return ApiResponse(ok=True, data={
        "message": f"一键全同步已提交（A股回填 {years} 年 → 指数 → 宏观 → 财报 → 外盘），独立进程后台执行中",
        "universe": universe,
        "years": years,
    })


@router.post("/sync-indices")
async def sync_indices_api():
    """同步指数数据到 qlib bin（独立 worker 后台执行）

    通过 akshare 拉取主要指数（上证、沪深300、中证500等）的日K行情，
    写入 qlib bin 格式。如遇日历中不存在的新日期会自动扩展日历。
    """
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)

    if writes_bins_active():
        return ApiResponse(ok=False, error={
            "code": "SYNC_IN_PROGRESS",
            "message": busy_message(),
            "status": 409,
        })

    from app.services.data.sync_worker import spawn_sync_worker
    spawn_sync_worker("indices", "indices")
    return ApiResponse(ok=True, data={"message": "指数同步已提交，独立进程后台执行中"})


@router.post("/sync-etf")
async def sync_etf_api(years: int = Query(None, description="回看年数，默认 2 年"),
                       days: int = Query(None, description="回看窗口（自然日），覆盖 years"),
                       overwrite: bool = Query(False, description="是否覆盖已有日期")):
    """同步全市场 ETF 日K 到 qlib bin（独立 worker 后台执行）。

    按交易日一次拉全市场（baostock query_daily_history_k_ETF），写 qlib bin
    （OHLCV+amount+change+tradable+factor），落 etf_daily 窄表，重建全量池
    instruments/etf_all.txt，并注册到 stock_index（type='etf'）。
    """
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)

    if writes_bins_active():
        return ApiResponse(ok=False, error={
            "code": "SYNC_IN_PROGRESS",
            "message": busy_message(),
            "status": 409,
        })

    # years（如前端"同步 X 年"输入框）转自然日；days 显式传入时优先
    if days is None:
        days = years * 365 if years else 730
    from app.services.data.sync_worker import spawn_sync_worker
    spawn_sync_worker("etf", "all", days=days, overwrite=overwrite)
    return ApiResponse(ok=True, data={"message": f"ETF 同步已提交（约 {days / 365:.1f} 年历史），独立进程后台执行中"})


@router.get("/universes")
async def list_universes_api():
    """列出可用标的池（instruments/*.txt：文件名 + 成分数）。

    供前端动态渲染标的池下拉（股票池 csi300/csi500/all、ETF 池 etf_all）。
    """
    import os
    from app.core.config import settings

    instruments_dir = os.path.join(settings.qlib_provider_path, "instruments")
    items = []
    if os.path.isdir(instruments_dir):
        for fname in sorted(os.listdir(instruments_dir)):
            if not fname.endswith(".txt"):
                continue
            name = fname[:-4]
            count = 0
            try:
                with open(os.path.join(instruments_dir, fname), encoding="utf-8") as f:
                    count = sum(1 for line in f if line.strip())
            except OSError:
                pass
            items.append({"name": name, "count": count})
    return ApiResponse(ok=True, data=items)


@router.get("/indices")
async def indices_api(db=Depends(get_db)):
    """已注册指数清单（stock_index 主表）：代码/名称/数据源 + qlib bin 状态。

    指数与股票是两类 instrument：指数只写 OHLCV 字段，没有 18 个股票
    BIN_FIELDS，也不在 stock_daily/财报中。数据校验通过本表区分二者，
    避免对指数按股票校验产生误报。
    """
    result = await db.execute(select(StockIndex).order_by(StockIndex.code))
    rows = result.scalars().all()
    feat_root = os.path.join(settings.qlib_provider_path, "features")
    items = []
    for r in rows:
        code_dir = os.path.join(feat_root, r.code)
        fields: list[str] = []
        if os.path.isdir(code_dir):
            fields = sorted(
                f[: -len(".day.bin")]
                for f in os.listdir(code_dir)
                if f.endswith(".day.bin")
            )
        items.append({
            "code": r.code,
            "name": r.name,
            "source": r.source,
            "type": r.type,  # index=指数 / etf=ETF
            "has_bin": os.path.isdir(code_dir),
            "bin_fields": fields,
        })
    return ApiResponse(ok=True, data={"items": items, "total": len(items)})


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
    if writes_bins_active():
        return ApiResponse(ok=False, error={
            "code": "SYNC_IN_PROGRESS",
            "message": busy_message("repair"),
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


@router.post("/fundamental/sync")
async def fundamental_sync_api(
    broadcast: bool = Query(False, description="是否同步后 PIT 广播写 qlib bin（建议数据校验/补齐阶段执行）"),
):
    """季频财报同步（akshare 逐股全量 → PG 窄表，独立 worker 后台执行）。

    akshare 财务摘要按股一次返回全部季度，全市场约 5400 次请求（2-3 小时）。
    broadcast=False（默认）只拉数据入库 PG，不写 bin——回填期间也安全；
    bin 广播（$roe/$netprofit_yoy 等）留到数据校验/补齐阶段（日历对齐后）。
    """
    from app.services.data.sync_worker import spawn_sync_worker

    if broadcast and writes_bins_active():
        return ApiResponse(ok=False, error={
            "code": "SYNC_IN_PROGRESS",
            "message": busy_message() + "；财报 bin 广播需等当前同步完成（日历对齐）后执行",
            "status": 409,
        })
    spawn_sync_worker("fundamental", "all", broadcast=broadcast)
    return ApiResponse(ok=True, data={
        "message": "财报同步已提交（独立进程后台执行，全市场逐股拉取）"
                   + ("" if not broadcast else "，含 PIT 广播写 bin"),
        "broadcast": broadcast,
    })
