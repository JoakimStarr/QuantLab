"""策略扩展 API：参数扫描、回测对比、交易明细导出、walk-forward 滚动回测、蒙特卡罗"""
import csv
import io
import json
import logging

import pandas as pd
from fastapi import APIRouter, Body, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.core.database import async_session
from app.core.errors import AppError
from app.models.task_result import TaskResult
from app.schemas.common import ApiResponse
from app.services.strategy import backtest_status
from app.services.strategy.manager import get_backtest_result

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/strategies", tags=["strategy-ext"])


class MonteCarloRequest(BaseModel):
    """回测指标 bootstrap 置信区间参数（可空，缺省用 config.monte_carlo）。"""
    n_iter: int | None = None
    block: int | None = None
    ci_level: float | None = None


async def _get_latest_task_result(strategy_id: int, task_type: str) -> TaskResult | None:
    """读取某策略某类型的最新任务结果。"""
    async with async_session() as session:
        result = await session.execute(
            select(TaskResult)
            .where(TaskResult.strategy_id == strategy_id, TaskResult.task_type == task_type)
            .order_by(TaskResult.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()


@router.get("/backtest-statuses")
async def list_backtest_statuses_api():
    """获取所有策略的回测状态。"""
    return ApiResponse(ok=True, data={"items": backtest_status.get_all_status()})


@router.post("/{strategy_id}/param-sweep")
async def param_sweep_api(
    strategy_id: int,
    topk_list: list[int] = Query([10, 20, 30, 50], description="topk 参数列表"),
    rebalance_list: list[str] = Query(["day", "week"], description="调仓频率列表"),
    start_date: str = Query(None),
    end_date: str = Query(None),
):
    """参数扫描（独立 worker 子进程执行，结果写 task_result 表）。"""
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)

    from app.core.config import settings
    period = settings.quant.get("default_backtest_period", {})
    start = start_date or period.get("start", "2020-01-01")
    end = end_date or period.get("end", "2024-12-31")

    from app.services.strategy.strategy_worker import is_task_running, spawn_strategy_worker
    if is_task_running("param-sweep", strategy_id):
        return ApiResponse(ok=True, data={
            "message": f"策略 {strategy_id} 参数扫描正在执行中，请勿重复提交",
            "strategy_id": strategy_id, "running": True,
        })

    # 创建持久化任务记录（前端轮询 task_result 获取结果/状态）
    async with async_session() as session:
        tr = TaskResult(strategy_id=strategy_id, task_type="param-sweep", status="running")
        session.add(tr)
        await session.commit()
        await session.refresh(tr)
        task_result_id = tr.id

    spawn_strategy_worker("param-sweep", strategy_id, {
        "task_result_id": task_result_id,
        "topk_list": topk_list, "rebalance_list": rebalance_list,
        "start": start, "end": end,
    })
    return ApiResponse(ok=True, data={
        "message": f"参数扫描已提交（{len(topk_list)} x {len(rebalance_list)} = {len(topk_list) * len(rebalance_list)} 组合，独立进程执行）",
        "strategy_id": strategy_id,
        "task_result_id": task_result_id,
        "topk_list": topk_list,
        "rebalance_list": rebalance_list,
    })


@router.get("/{strategy_id}/param-sweep-results")
async def param_sweep_results_api(strategy_id: int):
    """获取参数扫描结果"""
    tr = await _get_latest_task_result(strategy_id, "param-sweep")
    if tr is None:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "无参数扫描结果", "status": 404})
    return ApiResponse(ok=True, data={
        "status": tr.status,
        "results": json.loads(tr.payload) if tr.payload else None,
        "error": tr.error,
    })


@router.post("/compare-backtests")
async def compare_backtests_api(
    result_ids: list[int] = Query(..., description="对比的回测结果 ID 列表"),
):
    """回测对比（添加9: 回测对比）"""
    if len(result_ids) < 2:
        raise AppError("VALIDATION_ERROR", "至少选择 2 个回测结果进行对比", 422)

    results = []
    nav_curves = []
    for rid in result_ids:
        r = await get_backtest_result(rid)
        if r:
            results.append(r)
            if r.get("nav_curve"):
                nav_curves.append({"result_id": rid, "curve": r["nav_curve"]})

    if len(results) < 2:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "部分回测结果不存在", "status": 404})

    # 对比表格
    comparison = []
    metrics_keys = ["annual_return", "annual_volatility", "sharpe", "sortino",
                    "max_drawdown", "calmar", "turnover", "win_rate",
                    "benchmark_return", "excess_return"]
    for r in results:
        row = {"result_id": r["id"], "strategy_id": r["strategy_id"],
               "name": r.get("name") or f"策略#{r['strategy_id']}",
               "start_date": r["start_date"], "end_date": r["end_date"]}
        for k in metrics_keys:
            row[k] = r.get(k)
        comparison.append(row)

    return ApiResponse(ok=True, data={
        "comparison": comparison,
        "nav_curves": nav_curves,
        "metrics_keys": metrics_keys,
    })


@router.get("/backtest-results/{result_id}/trades")
async def export_trades_api(result_id: int):
    """交易明细导出（添加10: 交易明细导出）"""
    r = await get_backtest_result(result_id)
    if r is None:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "回测结果不存在", "status": 404})

    # 逐笔成交明细（新列）；旧结果回退从 metrics 读取
    trades = r.get("trades") or []
    if not trades:
        metrics = r.get("metrics") or {}
        trades = metrics.get("trades") or []

    if not trades:
        # 如果没有交易明细，从净值曲线生成持仓变动记录
        nav_curve = r.get("nav_curve") or []
        trades = []
        for i in range(1, len(nav_curve)):
            prev = nav_curve[i - 1]
            curr = nav_curve[i]
            daily_ret = (curr.get("nav", 1) / prev.get("nav", 1) - 1) if prev.get("nav") else 0
            trades.append({
                "date": curr.get("date", ""),
                "nav": round(curr.get("nav", 0), 4),
                "daily_return": round(daily_ret, 4),
                "benchmark_nav": round(curr.get("benchmark", 0), 4),
                "excess": round(daily_ret - (
                    (curr.get("benchmark", 1) / prev.get("benchmark", 1) - 1) if prev.get("benchmark") else 0
                ), 4),
            })

    # 生成 CSV
    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output)
    if trades:
        writer.writerow(trades[0].keys())
        for t in trades:
            writer.writerow(t.values())

    content = output.getvalue()
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=backtest_{result_id}_trades.csv"},
    )


# ---------------- 组合绩效报告（quantstats） ----------------

@router.post("/{strategy_id}/portfolio-report")
async def portfolio_report_api(
    strategy_id: int,
    result_id: int = Query(None, description="指定回测结果 ID（默认最新）"),
    generate_html: bool = Query(True, description="是否生成 HTML tear-sheet"),
):
    """组合绩效报告：quantstats 全量指标 + HTML tear-sheet。"""
    from app.services.quant.portfolio_report import generate_portfolio_report

    if result_id is None:
        from app.services.strategy.manager import list_backtest_results
        results = await list_backtest_results(strategy_id, limit=1)
        if not results:
            return ApiResponse(ok=False, error={"code": "NOT_FOUND",
                                                "message": "该策略暂无回测结果", "status": 404})
        # 列表接口返回轻量摘要（不含 nav_curve），组合报告需完整净值曲线，按 id 拉全量。
        r = await get_backtest_result(results[0].get("id")) or results[0]
    else:
        r = await get_backtest_result(result_id)
        if r is None:
            return ApiResponse(ok=False, error={"code": "NOT_FOUND",
                                                "message": "回测结果不存在", "status": 404})

    nav_curve = r.get("nav_curve") or {}
    dates, portfolio = None, None
    if isinstance(nav_curve, dict):
        dates = nav_curve.get("dates") or []
        portfolio = nav_curve.get("portfolio") or []
    elif isinstance(nav_curve, list):
        dates = [p.get("date") for p in nav_curve]
        portfolio = [p.get("nav") for p in nav_curve]
    if not dates or not portfolio or len(dates) != len(portfolio):
        return ApiResponse(ok=False, error={"code": "NO_NAV",
                                            "message": "回测结果缺少净值曲线数据", "status": 422})

    nav = pd.Series(portfolio, index=pd.to_datetime(dates)).astype(float)
    returns = nav.pct_change().dropna()
    if returns.empty:
        return ApiResponse(ok=False, error={"code": "NO_RETURNS",
                                            "message": "净值曲线过短，无法生成报告", "status": 422})

    # 重建基准收益（若存储了 benchmark 序列）
    benchmark = None
    if isinstance(nav_curve, dict) and nav_curve.get("benchmark"):
        b_nav = pd.Series(nav_curve["benchmark"], index=pd.to_datetime(dates)).astype(float)
        benchmark = b_nav.pct_change().dropna()

    report = generate_portfolio_report(
        returns, benchmark=benchmark,
        title=f"策略 {strategy_id} 组合绩效报告（回测 {r.get('start_date')}~{r.get('end_date')}）",
        generate_html=generate_html,
    )
    report["strategy_id"] = strategy_id
    report["result_id"] = r.get("id")
    report["period"] = {"start": r.get("start_date"), "end": r.get("end_date")}
    return ApiResponse(ok=True, data=report)


# ---------------- 蒙特卡罗模拟：回测指标 bootstrap 置信区间 ----------------

@router.post("/backtest-results/{result_id}/monte-carlo")
async def monte_carlo_api(
    result_id: int,
    payload: MonteCarloRequest = Body(default=None),
):
    """回测指标 bootstrap 置信区间（实时按需计算，不改库）。

    从回测结果的 nav_curve 重建日收益，做 stationary block bootstrap，
    输出 Sharpe/CAGR/最大回撤/胜率等核心指标的分布与 90% 置信区间，
    回答"这条回测曲线的指标是不是靠运气"。
    """
    from app.core.config import settings
    from app.core.executor import run_io_cpu
    from app.services.quant.monte_carlo import (
        bootstrap_metric_ci,
        mc_cache_get,
        mc_cache_set,
    )

    r = await get_backtest_result(result_id)
    if r is None:
        return ApiResponse(ok=False, error={
            "code": "NOT_FOUND", "message": "回测结果不存在", "status": 404})

    nav_curve = r.get("nav_curve") or {}
    dates, portfolio = None, None
    if isinstance(nav_curve, dict):
        dates = nav_curve.get("dates") or []
        portfolio = nav_curve.get("portfolio") or []
    elif isinstance(nav_curve, list):
        dates = [p.get("date") for p in nav_curve]
        portfolio = [p.get("nav") for p in nav_curve]
    if not dates or not portfolio or len(dates) != len(portfolio):
        return ApiResponse(ok=False, error={
            "code": "NO_NAV", "message": "回测结果缺少净值曲线数据", "status": 422})

    nav = pd.Series(portfolio, index=pd.to_datetime(dates)).astype(float)
    returns = nav.pct_change().dropna()
    if len(returns) == 0:
        return ApiResponse(ok=False, error={
            "code": "NO_RETURNS", "message": "净值曲线过短，无法模拟", "status": 422})

    mc = settings.monte_carlo
    n_iter = payload.n_iter if payload and payload.n_iter else mc.bootstrap_iterations
    block = payload.block if payload and payload.block else mc.bootstrap_block
    ci_level = payload.ci_level if payload and payload.ci_level else mc.bootstrap_ci
    # 进程内 LRU 缓存：同 (result_id, 参数) 命中免重算（backtest_result 行不可变）
    cache_key = (result_id, n_iter, block, ci_level)
    cached = mc_cache_get(cache_key)
    if cached is not None:
        return ApiResponse(ok=True, data=cached)

    # 线程池执行（不用 run_cpu 进程池，避免 reload 关停时 atexit join 卡死）；
    # bootstrap 为 numpy 计算，单线程可接受，且有 LRU 缓存兜底
    result = await run_io_cpu(
        bootstrap_metric_ci,
        returns.to_numpy(),
        n_iter=n_iter,
        block=block,
        ci_level=ci_level,
        seed=42,
    )
    result["result_id"] = result_id
    result["period"] = {"start": r.get("start_date"), "end": r.get("end_date")}
    mc_cache_set(cache_key, result)
    return ApiResponse(ok=True, data=result)


# ---------------- Walk-forward 滚动回测（添加14） ----------------

@router.post("/{strategy_id}/walk-forward")
async def walk_forward_api(
    strategy_id: int,
    train_window: str = Query("730D", description="训练窗口（如 730D≈2年）"),
    test_window: str = Query("180D", description="测试窗口（如 180D≈6月）"),
    step: str = Query("180D", description="滚动步长"),
    topk_list: list[int] = Query(None, description="候选 topk 列表，默认 [10,20,30,50]"),
    n_drop: int = Query(5, description="每期剔除数"),
    rebalance: str = Query("day", description="调仓频率 day/week/month"),
    universe: str = Query(None, description="标的池 csi300/csi500/all/etf_all"),
):
    """Walk-forward 滚动回测（独立 worker 子进程执行，结果写 task_result 表）。"""
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)

    from app.models.strategy import Strategy
    async with async_session() as session:
        s = await session.get(Strategy, strategy_id)
    if s is None:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "策略不存在", "status": 404})

    # 快速校验：策略有有效因子才提交（worker 内会重新加载因子表达式）
    from app.services.strategy.manager import _load_factor_expressions
    factor_ids = json.loads(s.factor_ids) if s.factor_ids else []
    factor_meta = await _load_factor_expressions(factor_ids)
    has_valid_expr = any(
        meta and not (meta.get("expression") or "").startswith(("AutoML(", "TextSentiment("))
        for meta in factor_meta.values()
    )
    if not has_valid_expr:
        raise AppError("VALIDATION_ERROR", "策略无有效因子", 422)

    topk_candidates = topk_list or [10, 20, 30, 50]

    from app.core.config import settings
    period = settings.quant.get("default_backtest_period", {})
    start = period.get("start", "2020-01-01")
    end = period.get("end", "2024-12-31")

    from app.services.strategy.strategy_worker import is_task_running, spawn_strategy_worker
    if is_task_running("walk-forward", strategy_id):
        return ApiResponse(ok=True, data={
            "message": f"策略 {strategy_id} Walk-forward 正在执行中，请勿重复提交",
            "strategy_id": strategy_id, "running": True,
        })

    # 创建持久化任务记录（前端轮询 task_result 获取结果/状态）
    async with async_session() as session:
        tr = TaskResult(strategy_id=strategy_id, task_type="walk-forward", status="running")
        session.add(tr)
        await session.commit()
        await session.refresh(tr)
        task_result_id = tr.id

    spawn_strategy_worker("walk-forward", strategy_id, {
        "task_result_id": task_result_id,
        "train_window": train_window, "test_window": test_window, "step": step,
        "topk_list": topk_candidates, "n_drop": n_drop, "rebalance": rebalance,
        "universe": universe, "start": start, "end": end,
    })
    return ApiResponse(ok=True, data={
        "message": "Walk-forward 滚动回测已提交（独立进程执行）",
        "strategy_id": strategy_id,
        "task_result_id": task_result_id,
        "train_window": train_window,
        "test_window": test_window,
        "step": step,
        "topk_candidates": topk_candidates,
    })


@router.get("/{strategy_id}/walk-forward-results")
async def walk_forward_results_api(strategy_id: int):
    """获取 walk-forward 回测结果（轮询）。"""
    tr = await _get_latest_task_result(strategy_id, "walk-forward")
    if tr is None:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "无 walk-forward 结果", "status": 404})
    return ApiResponse(ok=True, data={
        "status": tr.status,
        "result": json.loads(tr.payload) if tr.payload else None,
        "error": tr.error,
    })


@router.post("/ai/generate")
async def ai_generate_strategy_api(
    universe: str = Query(None, description="股票池"),
    start: str = Query(None, description="回测起始日期"),
    end: str = Query(None, description="回测结束日期"),
    factor_ids: list[int] = Query(None, description="偏好因子 ID 子集"),
    style: str = Query(None, description="偏好风格: momentum/reversal/lowvol/value/growth/volprice"),
    risk_tolerance: str = Query(None, description="风险偏好: conservative/balanced/aggressive"),
    rebalance_pref: str = Query(None, description="调仓频率偏好: day/week/month"),
    capital: float = Query(None, description="初始资金（元），供 AI 权衡 topk/换手/流动性"),
    other: str = Query(None, description="其他要求（自由文本），AI 自动权衡并说明取舍"),
):
    """AI 生成策略：参考因子库评价自动推荐因子组合与参数，创建策略。"""
    from app.services.strategy.ai_strategy import generate_strategy_with_ai
    try:
        result = await generate_strategy_with_ai(
            universe=universe, start=start, end=end,
            prefer_factor_ids=factor_ids,
            style=style, risk_tolerance=risk_tolerance, rebalance_pref=rebalance_pref,
            capital=capital, other=other,
        )
        return ApiResponse(ok=True, data=result)
    except ValueError as e:
        raise AppError("AI_STRATEGY_ERROR", str(e), 400)
    except Exception as e:
        logger.exception("AI 生成策略失败")
        raise AppError("AI_STRATEGY_ERROR", f"AI 生成策略失败: {e}", 500)


@router.post("/{strategy_id}/ai/params")
async def ai_suggest_params_api(strategy_id: int):
    """AI 参数建议：基于因子组合（+历史回测）推荐参数范围。"""
    from app.services.strategy.ai_strategy import suggest_params_with_ai
    try:
        result = await suggest_params_with_ai(strategy_id)
        return ApiResponse(ok=True, data=result)
    except ValueError as e:
        raise AppError("AI_STRATEGY_ERROR", str(e), 400)
    except Exception as e:
        logger.exception("AI 参数建议失败")
        raise AppError("AI_STRATEGY_ERROR", f"AI 参数建议失败: {e}", 500)


@router.post("/{strategy_id}/ai/review")
async def ai_review_backtest_api(
    strategy_id: int,
    result_id: int = Query(None, description="指定回测结果 ID（默认最新）"),
):
    """AI 策略复盘：解读回测结果生成文字报告。"""
    from app.services.strategy.ai_strategy import review_backtest_with_ai
    try:
        result = await review_backtest_with_ai(strategy_id, result_id=result_id)
        return ApiResponse(ok=True, data=result)
    except ValueError as e:
        raise AppError("AI_STRATEGY_ERROR", str(e), 400)
    except Exception as e:
        logger.exception("AI 策略复盘失败")
        raise AppError("AI_STRATEGY_ERROR", f"AI 策略复盘失败: {e}", 500)
