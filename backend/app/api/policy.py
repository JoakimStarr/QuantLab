"""政策风向 API：手动触发同步、查询列表（关键词/日期/翻页）、状态。"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, func, or_, select

from app.core.database import get_db
from app.schemas.common import ApiResponse
from app.models.policy import PolicyAnalysis, PolicyNews

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/policy", tags=["policy"])


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
    """手动触发 AI 政策解读（对「有新闻无解读」的日期生成结构化解读，独立 worker 后台执行）。"""
    from app.services.data.sync_worker import spawn_sync_worker
    spawn_sync_worker("policy_ai", "policy_ai", days=backfill_days)
    return ApiResponse(ok=True, data={"message": f"AI 政策解读已提交（回填 {backfill_days} 天，后台执行）"})


@router.get("/list")
async def policy_list_api(
    start: str = Query(None, description="开始日期 YYYY-MM-DD（按播出日期）"),
    end: str = Query(None, description="结束日期 YYYY-MM-DD（按播出日期）"),
    keyword: str = Query(None, description="标题关键词（模糊匹配）"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db=Depends(get_db),
):
    """政策风向列表：按播出日期倒序 + 关键词过滤（标题/正文/AI关键词）+ 分页。"""
    query = select(PolicyNews, PolicyAnalysis)
    if start:
        query = query.where(PolicyNews.news_date >= datetime.strptime(start, "%Y-%m-%d").date())
    if end:
        query = query.where(PolicyNews.news_date <= datetime.strptime(end, "%Y-%m-%d").date())
    if keyword:
        kw = f"%{keyword.strip()}%"
        query = query.where(or_(
            PolicyNews.title.ilike(kw),
            PolicyNews.content.ilike(kw),
            PolicyAnalysis.keywords.cast(String).ilike(kw),
        ))
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
        "ai_analyzed": bool(r.PolicyAnalysis and r.PolicyAnalysis.status == "done"),
    } for r in rows]
    return ApiResponse(ok=True, data={"items": items, "total": total, "page": page, "page_size": page_size})


@router.get("/status")
async def policy_status_api(db=Depends(get_db)):
    """政策风向数据状态：总条数、覆盖天数、最新/最早播出日期。"""
    latest = (await db.execute(select(func.max(PolicyNews.news_date)))).scalar()
    earliest = (await db.execute(select(func.min(PolicyNews.news_date)))).scalar()
    total = (await db.execute(select(func.count()).select_from(PolicyNews))).scalar() or 0
    days = (await db.execute(select(func.count(func.distinct(PolicyNews.news_date))))).scalar() or 0
    ai_done = (await db.execute(select(func.count()).select_from(PolicyAnalysis)
                              .where(PolicyAnalysis.status == "done"))).scalar() or 0
    ai_failed = (await db.execute(select(func.count()).select_from(PolicyAnalysis)
                                .where(PolicyAnalysis.status == "failed"))).scalar() or 0
    return ApiResponse(ok=True, data={
        "total": total,
        "days": days,
        "latest_date": latest.isoformat() if latest else None,
        "earliest_date": earliest.isoformat() if earliest else None,
        "ai_done": ai_done,
        "ai_failed": ai_failed,
    })


@router.get("/ai/detail")
async def policy_ai_detail_api(
    date: str = Query(..., description="播出日期 YYYY-MM-DD"),
    db=Depends(get_db),
):
    """某一天的 AI 政策解读（无解读返回 data=None）。"""
    a = (await db.execute(
        select(PolicyAnalysis).where(PolicyAnalysis.news_date == datetime.strptime(date, "%Y-%m-%d").date())
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
        query = query.where(PolicyAnalysis.news_date >= datetime.strptime(start, "%Y-%m-%d").date())
    if end:
        query = query.where(PolicyAnalysis.news_date <= datetime.strptime(end, "%Y-%m-%d").date())
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