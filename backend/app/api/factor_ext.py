"""因子扩展 API：对比、衰减分析、导出、自动入库"""
import csv
import io
import json
import logging

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.core.cache import TTLCache
from app.core.errors import AppError
from app.core.executor import run_io_cpu
from app.schemas.common import ApiResponse
from app.services.factor.factor_compare import compare_factors, compute_ic_correlation_matrix, get_factor_decay
from app.services.factor.library import get_factor, list_factors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/factors", tags=["factor-ext"])

# 分层收益/中性化都是全市场 qlib 重活（秒级），同一因子同参数短时间重复
# 调用直接复用（与 deep-analysis 的 1h 缓存同思路，TTL 10min 足够交互场景）
_quantile_cache = TTLCache(ttl=600, maxsize=64)
_neutralize_cache = TTLCache(ttl=600, maxsize=64)


# ---------- 因子评价后台任务（eval-jobs：长计算走独立 worker，可离开页面） ----------

@router.post("/eval-jobs")
async def create_eval_job_api(
    factor_ids: list[int] = Query(..., description="要评价/补算的因子 ID 列表"),
    kind: str = Query("batch", pattern="^(single|batch)$"),
    start_date: str = Query(None),
    end_date: str = Query(None),
    universe: str = Query(None, description="标的池 csi300/csi500/all/etf_all"),
):
    """创建后台因子评价任务：立即返回 job，worker 子进程逐个评价并可轮询进度/取消。

    替代"同步长请求"（原 /factors/{id}/evaluate 与 /factors/backfill-alpha158-metrics
    会在请求里跑完整分钟级计算），让用户发起后即可离开页面。
    """
    from app.core.config import settings
    from app.services.quant.qlib_init import is_qlib_available

    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装或行情数据未同步", 503)
    if not factor_ids:
        raise AppError("VALIDATION_ERROR", "至少选择一个因子", 422)
    from app.services.factor.eval_jobs import create_eval_job, ensure_eval_capacity

    cap = await ensure_eval_capacity()
    if cap:
        raise AppError("SYNC_IN_PROGRESS", cap, 409)
    period = settings.quant.get("default_backtest_period", {})
    start = start_date or period.get("start", "2020-01-01")
    end = end_date or period.get("end", "2024-12-31")
    job = await create_eval_job(kind, factor_ids, start, end, universe)
    from app.core.logging_config import get_request_id
    from app.services.factor.factor_eval_worker import spawn_factor_eval_worker

    spawn_factor_eval_worker(job["id"], request_id=get_request_id())
    return ApiResponse(ok=True, data={
        **job, "message": "评价任务已提交，后台计算中，完成后列表自动刷新",
    })


@router.get("/eval-jobs")
async def list_eval_jobs_api(limit: int = Query(20, ge=1, le=100)):
    """评价任务列表（轻量摘要，不含 result 大字段）。"""
    from app.services.factor.eval_jobs import list_eval_jobs

    items, total = await list_eval_jobs(limit=limit)
    return ApiResponse(ok=True, data={"items": items, "total": total})


@router.get("/eval-jobs/{job_id}")
async def get_eval_job_api(job_id: int):
    from app.services.factor.eval_jobs import get_eval_job

    job = await get_eval_job(job_id)
    if job is None:
        raise AppError("NOT_FOUND", "评价任务不存在", 404)
    return ApiResponse(ok=True, data=job)


@router.post("/eval-jobs/{job_id}/cancel")
async def cancel_eval_job_api(job_id: int):
    from app.services.factor.eval_jobs import cancel_eval_job

    if not await cancel_eval_job(job_id):
        raise AppError("NOT_FOUND", "评价任务不存在", 404)
    return ApiResponse(ok=True, data={"id": job_id, "status": "cancelled",
                                      "message": "已请求取消，将在当前因子计算完后停止"})


@router.post("/compare")
async def compare_factors_api(
    factor_ids: list[int] = Query(..., description="对比的因子 ID 列表"),
    start_date: str = Query(None),
    end_date: str = Query(None),
):
    """因子对比（添加5: 因子对比）"""
    from app.core.config import settings
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)
    if len(factor_ids) < 2:
        raise AppError("VALIDATION_ERROR", "至少选择 2 个因子进行对比", 422)
    period = settings.quant.get("default_backtest_period", {})
    start = start_date or period.get("start", "2020-01-01")
    end = end_date or period.get("end", "2024-12-31")
    result = await compare_factors(factor_ids, start, end)
    return ApiResponse(ok=True, data=result)


@router.get("/correlation-matrix")
async def factor_correlation_matrix_api(
    factor_ids: str = Query(..., description="逗号分隔的因子 ID，≤20 个"),
    start_date: str = Query(None),
    end_date: str = Query(None),
    universe: str = Query(None, description="标的池 csi300/csi500/all/etf_all"),
):
    """因子 IC 相关矩阵：对齐各因子日度 IC 公共日期计算两两相关系数。

    matrix 中重叠交易日不足（<20 天）或零方差处为 null。
    """
    from app.core.config import settings
    from app.services.quant.qlib_init import is_qlib_available

    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)
    try:
        ids = [int(x.strip()) for x in factor_ids.split(",") if x.strip()]
    except ValueError:
        raise AppError("VALIDATION_ERROR", "factor_ids 必须为逗号分隔的整数", 422) from None
    if not ids:
        raise AppError("VALIDATION_ERROR", "至少提供一个因子", 422)
    if len(ids) > 20:
        raise AppError("VALIDATION_ERROR", "最多支持 20 个因子", 422)
    period = settings.quant.get("default_backtest_period", {})
    start = start_date or period.get("start", "2020-01-01")
    end = end_date or period.get("end", "2024-12-31")
    result = await compute_ic_correlation_matrix(ids, start, end, universe)
    if "error" in result:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": result["error"], "status": 404})
    return ApiResponse(ok=True, data=result)


@router.get("/decay-check")
async def decay_check_api():
    """手动触发因子衰减检测（添加13: 因子衰减监控）"""
    from app.core.database import async_session
    from app.services.quant.factor_monitor import detect_all_factors_decay

    async with async_session() as session:
        result = await detect_all_factors_decay(db_session=session)
    return ApiResponse(ok=True, data=result)


@router.get("/{factor_id}/decay")
async def factor_decay_api(factor_id: int, max_lag: int = Query(20, le=40)):
    """因子 IC 衰减分析（添加6: 因子衰减分析）"""
    result = await get_factor_decay(factor_id, max_lag)
    if "error" in result:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": result["error"], "status": 404})
    return ApiResponse(ok=True, data=result)


@router.get("/export")
async def export_factors_api(
    category: str = Query(None),
    status: str = Query("active"),
    format: str = Query("csv", pattern="^(csv|json)$"),
):
    """因子导出（添加7: 因子导出）"""
    items, total = await list_factors(category=category, status=status, limit=500)
    if format == "json":
        import json
        content = json.dumps(items, ensure_ascii=False, indent=2, default=str)
        return StreamingResponse(
            io.BytesIO(content.encode("utf-8")),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=factors.json"},
        )
    # CSV
    output = io.StringIO()
    output.write("\ufeff")  # BOM for Excel
    writer = csv.writer(output)
    writer.writerow(["ID", "名称", "类别", "表达式", "IC", "RankIC", "ICIR", "换手", "状态", "创建时间"])
    for f in items:
        writer.writerow([
            f.get("id"), f.get("name"), f.get("category"),
            f.get("expression"), f.get("ic"), f.get("rank_ic"),
            f.get("icir"), f.get("turnover"), f.get("status"),
            f.get("created_at", ""),
        ])
    content = output.getvalue()
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=factors.csv"},
    )


@router.post("/auto-import")
async def auto_import_factors_api(
    task_id: int = Query(..., description="挖掘任务 ID"),
    ic_threshold: float = Query(0.03, description="IC 达标阈值"),
):
    """因子自动入库：从挖掘任务中导入 IC 达标的因子（添加12: 因子自动入库）"""
    from app.core.database import async_session
    from app.models.mining_task import MiningTask

    async with async_session() as session:
        task = await session.get(MiningTask, task_id)
        if task is None:
            return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "任务不存在", "status": 404})
        if task.status != "done":
            return ApiResponse(ok=False, error={"code": "TASK_NOT_DONE", "message": "任务尚未完成", "status": 400})
        result_ids = json.loads(task.result_factor_ids) if task.result_factor_ids else []

    if not result_ids:
        return ApiResponse(ok=False, error={"code": "NO_FACTORS", "message": "任务无结果因子", "status": 400})

    # 检查哪些因子已入库，哪些需要导入（单次 IN 查询，避免逐 ID N+1）
    from sqlalchemy import select
    from app.models.factor import Factor

    async with async_session() as session:
        rows = await session.execute(
            select(Factor.id, Factor.name, Factor.ic).where(Factor.id.in_(result_ids))
        )
        existing_map = {r.id: {"id": r.id, "name": r.name, "ic": r.ic} for r in rows.all()}

    imported = []
    skipped = []
    for fid in result_ids:
        existing = existing_map.get(fid)
        if existing:
            # 已入库，检查 IC 是否达标
            if existing.get("ic") and abs(existing["ic"]) >= ic_threshold:
                existing["status"] = "verified"
                imported.append({"id": fid, "name": existing["name"], "ic": existing["ic"], "action": "verified"})
            else:
                skipped.append({"id": fid, "ic": existing.get("ic"), "reason": "IC 未达标"})
        else:
            skipped.append({"id": fid, "reason": "因子不存在"})

    return ApiResponse(ok=True, data={
        "task_id": task_id,
        "imported": imported,
        "skipped": skipped,
        "total_imported": len(imported),
    })


@router.post("/seed-alpha158")
async def seed_alpha158_api():
    """导入 Alpha158 基准因子集（158 个 qlib 标准因子）。

    通过 WebSocket 推送 `alpha158_progress` 事件，包含 done/total/message 字段，
    前端可订阅 ws://<host>/ws 接收实时进度。
    """
    from app.core.websocket_manager import ws_manager
    from app.services.factor.alpha158 import seed_alpha158

    async def progress_cb(done: int, total: int, msg: str):
        await ws_manager.broadcast("alpha158_progress", {
            "done": done, "total": total, "message": msg,
        })

    result = await seed_alpha158(progress_callback=progress_cb)
    # 广播完成事件，方便前端区分 done/progress
    try:
        await ws_manager.broadcast("alpha158_progress", {
            "done": result.get("evaluated", 0),
            "total": result.get("count") or result.get("total") or 158,
            "message": result.get("message", "完成"),
            "finished": True,
        })
    except Exception:
        pass

    if not result.get("ok"):
        return ApiResponse(ok=False, error={
            "code": "ALPHA158_SEEDED", "message": result.get("error", "已导入"), "status": 400,
        })
    return ApiResponse(ok=True, data=result)


@router.post("/etf/seed")
async def seed_etf_factors_api():
    """导入内置 ETF 因子集（OHLCV-only，category='etf'，供 ETF 标的池使用）。

    只导入不评价；评价需在 ETF 池（etf_all）上通过补算指标/单因子评价触发。
    """
    from app.services.factor.etf_factors import seed_etf_factors

    result = await seed_etf_factors()
    if not result.get("ok"):
        return ApiResponse(ok=False, error={
            "code": "ETF_SEED_FAILED", "message": result.get("error", "导入失败"), "status": 400,
        })
    return ApiResponse(ok=True, data=result)


@router.post("/backfill-alpha158-metrics")
async def backfill_alpha158_metrics_api(
    factor_ids: list[int] = Query(None, description="指定重算的因子 ID 列表；不传则只补算缺指标的 Alpha158 因子"),
    start_date: str = Query(None, description="评价区间起始日期"),
    end_date: str = Query(None, description="评价区间结束日期"),
    universe: str = Query(None, description="标的池 csi300/csi500/all/etf_all"),
):
    """为因子补算评价（IC/RankIC/ICIR/turnover）。

    传 factor_ids：仅重算所选因子的指标（支持任意类别，覆盖已有值）。
    不传：只补算历史遗留中指标为 NULL 的 Alpha158 因子。
    start_date/end_date：指定评价区间（不传则用默认回测区间）。
    universe：标的池（默认 config.quant.universe，可选 etf_all 评价 ETF 因子）。
    进度通过 WebSocket `alpha158_progress` 事件推送。
    """
    from app.core.websocket_manager import ws_manager
    from app.services.factor.alpha158 import backfill_alpha158_metrics

    async def progress_cb(done: int, total: int, msg: str):
        await ws_manager.broadcast("alpha158_progress", {
            "done": done, "total": total, "message": msg,
        })

    result = await backfill_alpha158_metrics(
        progress_callback=progress_cb, factor_ids=factor_ids,
        eval_start=start_date, eval_end=end_date, universe=universe,
    )
    try:
        await ws_manager.broadcast("alpha158_progress", {
            "done": result.get("evaluated", 0),
            "total": result.get("total", 0),
            "message": result.get("message", "补算完成"),
            "finished": True,
        })
    except Exception:
        pass

    return ApiResponse(ok=result.get("ok", True), data=result)


@router.get("/{factor_id}/quantile-analysis")
async def quantile_analysis_api(
    factor_id: int,
    n_groups: int = Query(5, ge=2, le=10),
    start_date: str = Query(None),
    end_date: str = Query(None),
):
    """因子分组收益评价（分层回测）：按因子值分 n_groups 组，返回各组净值、多空收益与单调性。"""
    from app.core.config import settings
    from app.services.factor.library import get_factor
    from app.services.quant.factor_eval import (
        compute_quantile_returns,
        load_close_prices,
        load_factor_values,
        load_label,
    )
    from app.services.quant.qlib_init import is_qlib_available

    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)
    factor = await get_factor(factor_id)
    if not factor:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "因子不存在", "status": 404})

    period = settings.quant.get("default_backtest_period", {})
    start = start_date or period.get("start", "2020-01-01")
    end = end_date or period.get("end", "2024-12-31")

    cache_key = (factor_id, start, end, n_groups)
    cached = _quantile_cache.get(cache_key)
    if cached is not None:
        return ApiResponse(ok=True, data=cached)

    def _compute_quantile():
        factor_df = load_factor_values(factor["expression"], start, end)
        return_df = load_label(start, end)
        prices_df = load_close_prices(start, end)
        return compute_quantile_returns(
            factor_df, return_df, n_groups=n_groups, prices_df=prices_df
        )

    try:
        result = await run_io_cpu(_compute_quantile)
    except Exception as e:
        logger.warning("分组收益数据加载失败 factor_id=%s: %s", factor_id, e)
        return ApiResponse(ok=False, error={"code": "DATA_LOAD_ERROR", "message": str(e), "status": 500})

    if "error" in result:
        return ApiResponse(ok=False, error={"code": "NO_DATA", "message": result["error"], "status": 400})
    _quantile_cache.set(cache_key, result)
    return ApiResponse(ok=True, data=result)


@router.post("/{factor_id}/neutralize")
async def neutralize_factor_api(
    factor_id: int,
    method: str = Query("market_cap", pattern="^(market_cap|industry|both)$"),
    start_date: str = Query(None),
    end_date: str = Query(None),
):
    """因子中性化：对比中性化前后 IC 指标

    method: market_cap(市值中性化) / industry(行业+市值中性化) / both(同 industry)
    """
    from app.core.config import settings
    from app.services.factor.library import get_factor
    from app.services.quant.factor_eval import (
        compute_ic,
        load_factor_values,
        load_label,
    )
    from app.services.quant.qlib_init import is_qlib_available

    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)
    factor = await get_factor(factor_id)
    if not factor:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "因子不存在", "status": 404})

    period = settings.quant.get("default_backtest_period", {})
    start = start_date or period.get("start", "2020-01-01")
    end = end_date or period.get("end", "2024-12-31")

    # 统一映射：both 等价于 industry（行业+市值）
    neutralize_method = "industry" if method in ("industry", "both") else "market_cap"

    cache_key = (factor_id, start, end, method)
    cached = _neutralize_cache.get(cache_key)
    if cached is not None:
        return ApiResponse(ok=True, data=cached)

    def _compute_neutralize():
        factor_df_before = load_factor_values(factor["expression"], start, end)
        label_df = load_label(start, end)
        ic_before = compute_ic(factor_df_before, label_df)
        # 复用已加载的因子值，在内存内套用中性化，避免同一表达式重复全量 qlib 读取
        market = settings.quant.get("universe", "csi300")
        if market.startswith("etf"):
            # 与 load_factor_values 一致：ETF 无市值/行业数据，跳过中性化
            factor_df_after = factor_df_before
        else:
            from app.services.factor.neutralize import industry_neutralize, market_cap_neutralize
            if neutralize_method == "market_cap":
                factor_df_after = market_cap_neutralize(factor_df_before, factor_col="factor")
            else:
                factor_df_after = industry_neutralize(factor_df_before, factor_col="factor")
            factor_df_after = factor_df_after.copy()
            factor_df_after["factor"] = factor_df_after["factor_neutralized"]
            factor_df_after = factor_df_after.drop(columns=["factor_neutralized"])
        ic_after = compute_ic(factor_df_after, label_df)
        return ic_before, ic_after

    try:
        ic_before, ic_after = await run_io_cpu(_compute_neutralize)
    except Exception as e:
        logger.warning("因子中性化失败 factor_id=%s: %s", factor_id, e)
        return ApiResponse(ok=False, error={"code": "NEUTRALIZE_ERROR", "message": str(e), "status": 500})

    data = {
        "factor_id": factor_id,
        "factor_name": factor.get("name"),
        "method": method,
        "ic_before": ic_before,
        "ic_after": ic_after,
        "eval_start": start,
        "eval_end": end,
    }
    _neutralize_cache.set(cache_key, data)
    return ApiResponse(ok=True, data=data)


# ==================== 因子深度分析 ====================
# 深度分析结果缓存：key=factor_id|start|end|horizon|n_groups|ic_window，TTL 1 小时、上限 64 条
_deep_analysis_cache = TTLCache(ttl=3600, maxsize=64)


@router.get("/{factor_id}/deep-analysis")
async def deep_analysis_api(
    factor_id: int,
    start_date: str = Query(None),
    end_date: str = Query(None),
    horizon: int = Query(5, ge=1, le=60),
    n_groups: int = Query(5, ge=2, le=10),
    ic_window: int = Query(60, ge=20, le=250),
    universe: str = Query(None, description="标的池 csi300/csi500/all/etf_all"),
):
    """因子深度分析：IC 分布/时序/显著性 + horizon 调仓分层净值 + 换手率曲线 + 衰减。"""
    from app.core.config import settings
    from app.services.quant.factor_eval import deep_analyze_factor
    from app.services.quant.qlib_init import is_qlib_available

    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)
    factor = await get_factor(factor_id)
    if not factor:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "因子不存在", "status": 404})

    period = settings.quant.get("default_backtest_period", {})
    start = start_date or period.get("start", "2020-01-01")
    end = end_date or period.get("end", "2024-12-31")
    universe = universe or settings.quant.get("universe", "csi300")

    # 缓存命中直接返回（含 factor_id/factor_name）
    cache_key = f"{factor_id}|{start}|{end}|{horizon}|{n_groups}|{ic_window}|{universe}"
    cached = _deep_analysis_cache.get(cache_key)
    if cached is not None:
        return ApiResponse(ok=True, data=cached)

    # 线程池执行：qlib C 扩展释放 GIL；不用 run_cpu（进程池）避免 reload 关停时
    # atexit join 进程池导致服务卡死（与因子评价同源事故）
    try:
        result = await run_io_cpu(
            deep_analyze_factor,
            factor["expression"], start, end, universe, horizon, n_groups, ic_window,
        )
    except ValueError as e:
        logger.warning("因子深度分析数据不足 factor_id=%s: %s", factor_id, e)
        return ApiResponse(ok=False, error={"code": "INSUFFICIENT_DATA", "message": str(e), "status": 400})
    except Exception as e:
        logger.warning("因子深度分析失败 factor_id=%s: %s", factor_id, e)
        return ApiResponse(ok=False, error={"code": "FACTOR_NOT_COMPUTABLE", "message": str(e), "status": 400})

    result["factor_id"] = factor_id
    result["factor_name"] = factor.get("name")
    _deep_analysis_cache.set(cache_key, result)
    return ApiResponse(ok=True, data=result)


@router.post("/{factor_id}/ai-explain")
async def ai_explain_factor_api(factor_id: int, force: bool = Query(False)):
    """AI 因子解释：为因子生成完整金融逻辑描述（幂等，force=True 强制重新生成）。

    写回 ai_explanation（结构化 JSON），description 只存 summary 一句话简述。
    """
    from app.services.factor.ai_explain import explain_and_update_factor
    try:
        result = await explain_and_update_factor(factor_id, force=force)
        return ApiResponse(ok=True, data=result)
    except ValueError as e:
        raise AppError("FACTOR_NOT_FOUND", str(e), 404) from None
    except Exception as e:
        logger.exception("AI 因子解释失败 factor_id=%s", factor_id)
        raise AppError("AI_EXPLAIN_ERROR", f"AI 因子解释失败: {e}", 500) from None


@router.post("/ai-explain-batch")
async def ai_explain_factors_batch_api(factor_ids: list[int] = Query(...), force: bool = Query(False)):
    """批量 AI 因子解释（幂等：已有解释且非 force 时跳过，不重复调 LLM）。"""
    from app.services.factor.ai_explain import explain_factors_batch
    results = await explain_factors_batch(factor_ids, force=force)
    return ApiResponse(ok=True, data={"items": results, "total": len(results)})


@router.get("/{factor_id}/ai-detail")
async def ai_detail_factor_api(factor_id: int):
    """获取因子的完整 AI 解释与追问历史（供前端弹窗展示）。"""
    from app.services.factor.ai_explain import get_factor_ai_detail
    try:
        result = await get_factor_ai_detail(factor_id)
        return ApiResponse(ok=True, data=result)
    except ValueError as e:
        raise AppError("FACTOR_NOT_FOUND", str(e), 404) from None
    except Exception as e:
        logger.exception("AI 详情获取失败 factor_id=%s", factor_id)
        raise AppError("AI_EXPLAIN_ERROR", f"AI 详情获取失败: {e}", 500) from None


@router.post("/{factor_id}/ai-chat")
async def ai_chat_factor_api(factor_id: int, payload: dict = None):
    """继续追问：基于已有 AI 解释回答用户问题，对话历史持久化。"""
    from app.services.factor.ai_explain import chat_followup
    question = (payload or {}).get("question", "")
    try:
        result = await chat_followup(factor_id, question)
        return ApiResponse(ok=True, data=result)
    except ValueError as e:
        raise AppError("AI_EXPLAIN_ERROR", str(e), 400) from None
    except Exception as e:
        logger.exception("AI 追问失败 factor_id=%s", factor_id)
        raise AppError("AI_EXPLAIN_ERROR", f"AI 追问失败: {e}", 500) from None
