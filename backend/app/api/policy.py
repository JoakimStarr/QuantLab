"""政策风向 API：手动触发同步、查询列表（关键词/日期/翻页）、状态、AI 任务进度。"""
import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, func, or_, select

from app.core.database import get_db
from app.models.policy import PolicyAnalysis, PolicyNews
from app.schemas.common import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/policy", tags=["policy"])


def _parse_date(value: str | None, name: str) -> date | None:
    """解析 YYYY-MM-DD 参数，格式非法返回 400（而不是 500）。"""
    if value is None:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{name} 日期格式非法: {value!r}，需要 YYYY-MM-DD") from None


@router.post("/sync")
async def policy_sync_api():
    """手动触发新闻联播同步（增量，独立 worker 后台执行；只存库不写 bin）。"""
    from app.services.data.sync_worker import spawn_sync_worker
    spawn_sync_worker("policy", "policy")
    return ApiResponse(ok=True, data={"message": "政策风向同步已提交（独立进程后台执行）"})


@router.post("/ai/sync")
async def policy_ai_sync_api(
    backfill_days: int = Query(30, ge=1, le=365, description="AI 解读回填窗口（天）"),
):
    """手动触发 AI 政策解读（对「有新闻无解读」的日期生成结构化解读，独立 worker 后台执行）。

    返回 pending_count：本次窗口内待处理天数，前端据此判断任务是否已结束，
    避免拿全历史口径的 status.ai_pending 当完成信号（那会导致轮询永不终止）。
    """
    from app.services.data.policy_ai import _pending_dates
    pending = await _pending_dates(backfill_days)

    from app.services.data.sync_worker import spawn_sync_worker
    spawn_sync_worker("policy_ai", "policy_ai", days=backfill_days)
    return ApiResponse(ok=True, data={
        "message": f"AI 政策解读已提交（回填 {backfill_days} 天，待处理 {len(pending)} 天，后台执行）",
        "pending_count": len(pending),
        "backfill_days": backfill_days,
    })


@router.get("/ai/progress")
async def policy_ai_progress_api():
    """AI 解读任务实时进度（worker 子进程写共享文件）。

    返回 {status, total, done, failed, started_at, error?}，status ∈ running/done/failed；
    无任务时返回 None。前端以终态（done/failed）作为本次任务完成的唯一信号。
    """
    from app.services.data.policy_ai_progress import get_progress
    return ApiResponse(ok=True, data=get_progress())


@router.get("/list")
async def policy_list_api(
    start: str = Query(None, description="开始日期 YYYY-MM-DD（按播出日期）"),
    end: str = Query(None, description="结束日期 YYYY-MM-DD（按播出日期）"),
    keyword: str = Query(None, description="标题关键词（模糊匹配）"),
    source: str = Query(None, description="数据源过滤：cctv/cjzc/em（空则全部）"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db=Depends(get_db),
):
    """政策风向列表：按播出日期倒序 + 关键词过滤（标题/正文/AI关键词）+ 分页。"""
    query = select(PolicyNews, PolicyAnalysis)
    if start:
        query = query.where(PolicyNews.news_date >= _parse_date(start, "start"))
    if end:
        query = query.where(PolicyNews.news_date <= _parse_date(end, "end"))
    if keyword:
        kw = f"%{keyword.strip()}%"
        query = query.where(or_(
            PolicyNews.title.ilike(kw),
            PolicyNews.content.ilike(kw),
            PolicyAnalysis.keywords.cast(String).ilike(kw),
        ))
    if source:
        query = query.where(PolicyNews.source == source)
    query = query.outerjoin(PolicyAnalysis, PolicyAnalysis.news_date == PolicyNews.news_date)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    rows = (await db.execute(
        query.order_by(PolicyNews.news_date.desc(), PolicyNews.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).all()
    items = [{
        "id": r.PolicyNews.id,
        "news_date": r.PolicyNews.news_date.isoformat(),
        "title": r.PolicyNews.title,
        "content": r.PolicyNews.content,
        "source": r.PolicyNews.source or "cctv",
        "ai_analyzed": bool(r.PolicyAnalysis and r.PolicyAnalysis.status == "done"),
    } for r in rows]
    return ApiResponse(ok=True, data={"items": items, "total": total, "page": page, "page_size": page_size})


@router.get("/status")
async def policy_status_api(db=Depends(get_db)):
    """政策风向数据状态：总条数、覆盖天数、最新/最早播出日期 + AI 解读进度。"""
    latest = (await db.execute(select(func.max(PolicyNews.news_date)))).scalar()
    earliest = (await db.execute(select(func.min(PolicyNews.news_date)))).scalar()
    total = (await db.execute(select(func.count()).select_from(PolicyNews))).scalar() or 0
    days = (await db.execute(select(func.count(func.distinct(PolicyNews.news_date))))).scalar() or 0
    ai_done = (await db.execute(select(func.count()).select_from(PolicyAnalysis)
                              .where(PolicyAnalysis.status == "done"))).scalar() or 0
    ai_failed = (await db.execute(select(func.count()).select_from(PolicyAnalysis)
                                .where(PolicyAnalysis.status == "failed"))).scalar() or 0
    # 待解读日期数：有新闻但尚无 done 解读的日期（含 failed，供前端展示进度）
    ai_pending = 0
    if latest is not None:
        sub = (
            select(PolicyNews.news_date.distinct().label("news_date"))
            .where(PolicyNews.news_date <= latest)
        ).subquery()
        done_sub = (
            select(PolicyAnalysis.news_date)
            .where(PolicyAnalysis.status == "done")
        ).subquery()
        ai_pending = (await db.execute(
            select(func.count()).select_from(sub).where(sub.c.news_date.notin_(select(done_sub.c.news_date)))
        )).scalar() or 0
    # 各数据源条数统计（cctv/cjzc/em…）
    breakdown_rows = (await db.execute(
        select(PolicyNews.source, func.count())
        .group_by(PolicyNews.source)
    )).all()
    source_breakdown = {r[0] or "cctv": r[1] for r in breakdown_rows}
    return ApiResponse(ok=True, data={
        "total": total,
        "days": days,
        "latest_date": latest.isoformat() if latest else None,
        "earliest_date": earliest.isoformat() if earliest else None,
        "ai_done": ai_done,
        "ai_failed": ai_failed,
        "ai_pending": ai_pending,
        "ai_total": days,  # 有新闻的日期总数（解读窗口 = ai_total）
        "source_breakdown": source_breakdown,
    })


@router.get("/schedule")
async def policy_schedule_api():
    """定时数据刷新配置（启用开关/每日时间/工作日限定/环节选择）。"""
    from app.services.task.sync_schedule_service import get_schedule
    return ApiResponse(ok=True, data=await get_schedule())


@router.put("/schedule")
async def policy_schedule_update_api(payload: dict):
    """更新定时数据刷新配置（单行 upsert），返回落库后的配置。"""
    from app.services.task.sync_schedule_service import save_schedule
    try:
        data = await save_schedule(payload)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return ApiResponse(ok=True, data=data)


@router.get("/ai/detail")
async def policy_ai_detail_api(
    date: str = Query(..., description="播出日期 YYYY-MM-DD"),
    db=Depends(get_db),
):
    """某一天的 AI 政策解读（无解读返回 data=None）。"""
    a = (await db.execute(
        select(PolicyAnalysis).where(PolicyAnalysis.news_date == _parse_date(date, "date"))
    )).scalar_one_or_none()
    if a is None:
        return ApiResponse(ok=True, data=None)
    return ApiResponse(ok=True, data=_analysis_to_dict(a))


def _analysis_to_dict(a: PolicyAnalysis) -> dict:
    """PolicyAnalysis → API dict（JSON 字段转原生 list）。"""
    return {
        "news_date": a.news_date.isoformat(),
        "status": a.status,
        "summary": a.summary,
        "policy_tone": a.policy_tone,
        "key_items": a.key_items,
        "sectors": a.sectors,
        "topics": a.topics,
        "keywords": a.keywords,
        "market_impact": a.market_impact,
        "error": a.error,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


@router.get("/latest")
async def policy_latest_api(
    days: int = Query(7, ge=1, le=60, description="最近 N 个已解读日期"),
    db=Depends(get_db),
):
    """最近 N 天已解读的政策定调（供顶部「当日政策定调」卡片）。

    返回 [{news_date, status, summary, policy_tone, key_items, sectors, topics, market_impact}]。
    """
    rows = (await db.execute(
        select(PolicyAnalysis)
        .where(PolicyAnalysis.status == "done")
        .order_by(PolicyAnalysis.news_date.desc())
        .limit(days)
    )).scalars().all()
    items = [{
        "news_date": a.news_date.isoformat(),
        "status": a.status,
        "summary": a.summary,
        "policy_tone": a.policy_tone,
        "key_items": a.key_items,
        "sectors": a.sectors,
        "topics": a.topics,
        "market_impact": a.market_impact,
    } for a in rows]
    return ApiResponse(ok=True, data={"items": items})


@router.get("/sectors/performance")
async def policy_sector_perf_api(
    days: int = Query(14, ge=1, le=60, description="回看最近 N 个有 AI 解读的日期"),
    db=Depends(get_db),
):
    """点名板块 × 市场表现：每天点名的板块（匹配到证监会行业后）T+1/T+3/T+5 成分股等权收益。"""
    from app.services.data.policy_sector_perf import compute_sector_performance
    data = await compute_sector_performance(days)
    return ApiResponse(ok=True, data=data)


@router.get("/ai/topics")
async def ai_topics_api(
    start: str = Query(None, description="开始日期 YYYY-MM-DD"),
    end: str = Query(None, description="结束日期 YYYY-MM-DD"),
    db=Depends(get_db),
):
    """政策主题热度序列：每天每主题 score（0~1），供前端热度图表。

    按主题聚合一天的分数（同主题多条目取 max），返回 [{date, topics: {topic: score}}]。
    """
    query = select(PolicyAnalysis.news_date, PolicyAnalysis.topics).where(PolicyAnalysis.status == "done")
    if start:
        query = query.where(PolicyAnalysis.news_date >= _parse_date(start, "start"))
    if end:
        query = query.where(PolicyAnalysis.news_date <= _parse_date(end, "end"))
    rows = (await db.execute(query.order_by(PolicyAnalysis.news_date))).all()
    if not rows:
        return ApiResponse(ok=True, data={"items": [], "total": 0})

    all_topics: dict[str, float] = {}
    items = []
    for d, topics in rows:
        topic_map: dict[str, float] = {}
        for t in topics or []:
            if isinstance(t, dict) and t.get("topic"):
                name = str(t["topic"])
                try:
                    score = float(t.get("score") or 0)
                except (TypeError, ValueError):
                    score = 0.0
                topic_map[name] = max(topic_map.get(name, 0.0), score)
        for name, score in topic_map.items():
            all_topics[name] = all_topics.get(name, 0) + score
        items.append({"date": d.isoformat(), "topics": topic_map})
    # 热度排序，供前端挑选展示主题
    top_topics = sorted(all_topics.items(), key=lambda kv: kv[1], reverse=True)
    return ApiResponse(ok=True, data={
        "items": items,
        "topic_rank": [{"topic": t, "score": round(s, 3)} for t, s in top_topics[:20]],
    })
