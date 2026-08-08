"""政策风向 AI 解读：每日新闻联播 → LLM 结构化解读。

一次 LLM 调用产出三类产物（写入 policy_analysis，一天一条）：
  1. 每日解读   summary / policy_tone / key_items / market_impact
  2. 题材标签   sectors（点名行业 + 利好/利空/中性 + 理由）
  3. 主题热度   topics[{topic, score}] + 关键词 keywords（供内容检索）

回填策略：处理「有新闻但尚无 done 解读」的日期（新→旧），窗口默认 30 天控制成本；
失败日期标记 failed，下次同步自动重试。LLM 走 ProviderRouter 多 provider failover。
"""
import asyncio
import logging
from datetime import date, timedelta

from sqlalchemy import func, select

from app.core.database import async_session
from app.models.policy import PolicyAnalysis, PolicyNews
from app.services.data.db_utils import bulk_upsert

logger = logging.getLogger(__name__)

AI_BACKFILL_DAYS = 30        # 默认回填窗口（天）
_MAX_CONTENT_CHARS = 400     # 每条新闻内容截断长度
_MAX_ITEMS = 24              # 每天最多送入 LLM 的新闻条数
_CONCURRENCY = 2             # LLM 并发数（保守，防限流）

SYSTEM_PROMPT = """你是 A 股的宏观政策研究员。用户会给你某一天的央视《新闻联播》文字稿列表（标题+正文前段）。
请输出 JSON（不要输出 JSON 之外的任何内容），结构：
{
  "summary": "150字以内当日政策解读摘要（覆盖最重要 1-3 件事，偏市场视角）",
  "policy_tone": "当日政策定调一句话（积极/稳健/收紧，对应什么方向）",
  "key_items": [{"title": "重磅条目标题", "impact": "对资本市场/A股的影响（50字内）"}],
  "sectors": [{"name": "行业/板块（如：AI算力、白酒、军工）", "direction": "利好/利空/中性", "reason": "为什么（30字内）"}],
  "topics": [{"topic": "政策主题词（如：人工智能、扩内需、对外开放）", "score": 0.0}],
  "keywords": ["5-12个中文关键词，覆盖当日政策的检索要点"],
  "market_impact": "对翌日 A 股市场的影响判断（不超过80字，突出方向与相关板块）"
}
要求：只依据给定文字稿，不编造；sectors 必须给出 direction；topics 的 score 为 0~1 小数，
表示当日该主题的政策力度与市场相关度。"""

_USER_TEMPLATE = """日期：{day}（{weekday}）

新闻联播条目：
{items}
"""


def _build_messages(day: date, news_rows: list[dict]) -> list[dict]:
    """组装 messages：截断长文，控制 token 预算。"""
    lines = []
    for i, r in enumerate(news_rows, 1):
        title = r["title"]
        content = r.get("content") or ""
        if len(content) > _MAX_CONTENT_CHARS:
            content = content[:_MAX_CONTENT_CHARS] + "…"
        lines.append(f"{i}. 【{title}】{content}")
    user = _USER_TEMPLATE.format(
        day=day.isoformat(),
        weekday="周" + "一二三四五六日"[day.weekday()],
        items="\n".join(lines),
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def _normalize(d: dict) -> dict:
    """清洗 LLM 输出：保证关键字段类型正确，容错缺失字段。"""
    ok_list = lambda v: v if isinstance(v, list) else None  # noqa: E731
    return {
        "summary": str(d.get("summary") or "").strip() or None,
        "policy_tone": str(d.get("policy_tone") or "").strip() or None,
        "key_items": ok_list(d.get("key_items")),
        "sectors": ok_list(d.get("sectors")),
        "topics": ok_list(d.get("topics")),
        "keywords": ok_list(d.get("keywords")),
        "market_impact": str(d.get("market_impact") or "").strip() or None,
    }


async def _day_news(news_date: date) -> list[dict]:
    async with async_session() as session:
        rows = (await session.execute(
            select(PolicyNews.title, PolicyNews.content)
            .where(PolicyNews.news_date == news_date)
            .order_by(PolicyNews.id)
            .limit(_MAX_ITEMS)
        )).all()
    return [{"title": r.title, "content": r.content} for r in rows]


async def analyze_one_day(news_date: date) -> dict:
    """对单日文字稿调用 LLM 产出解读（网络 IO）。"""
    from app.services.ai.llm_json import call_llm_json

    items = await _day_news(news_date)
    if not items:
        raise ValueError(f"{news_date} 无新闻数据")
    result = await call_llm_json(_build_messages(news_date, items))
    return _normalize(result)


async def _pending_dates(max_days: int) -> list[date]:
    """最近 max_days 内「有新闻但无 done 解读」的日期（新→旧）。"""
    async with async_session() as session:
        latest = (await session.execute(select(func.max(PolicyNews.news_date)))).scalar()
        if latest is None:
            return []
        start = latest - timedelta(days=max_days)
        news_dates = set((await session.execute(
            select(PolicyNews.news_date).where(PolicyNews.news_date >= start)
        )).scalars())
        done_dates = set((await session.execute(
            select(PolicyAnalysis.news_date).where(
                PolicyAnalysis.news_date >= start, PolicyAnalysis.status == "done")
        )).scalars())
    return sorted(news_dates - done_dates, reverse=True)


async def sync_policy_analysis(backfill_days: int = AI_BACKFILL_DAYS,
                               progress_cb=None) -> dict:
    """对「有新闻但无解读」的日期调用 LLM 补齐解读。

    Args:
        backfill_days: 回填窗口（天）
        progress_cb: 可选 progress_cb(idx, total, date_str)
    Returns: {"days": 处理天数, "done": 成功数, "failed": 失败数}
    """
    pending = await _pending_dates(backfill_days)
    if not pending:
        return {"days": 0, "done": 0, "failed": 0}
    logger.info("AI 政策解读待处理 %d 天: %s ... %s", len(pending), pending[-1], pending[0])

    sem = asyncio.Semaphore(_CONCURRENCY)

    async def process(d: date) -> dict:
        async with sem:
            try:
                parsed = await analyze_one_day(d)
                return {"news_date": d, "status": "done", **parsed, "error": None}
            except Exception as e:
                logger.warning("AI 解读 %s 失败: %s", d, e)
                return {"news_date": d, "status": "failed", "summary": None,
                        "policy_tone": None, "key_items": None, "sectors": None,
                        "topics": None, "keywords": None, "market_impact": None,
                        "error": str(e)[:500]}

    rows = []
    for i, d in enumerate(pending):
        if progress_cb:
            progress_cb(i + 1, len(pending), d.isoformat())
        rows.append(await process(d))

    written = await bulk_upsert(
        PolicyAnalysis, rows, ["news_date"], batch=50,
        update_cols=["status", "summary", "policy_tone", "key_items", "sectors",
                     "topics", "keywords", "market_impact", "error"],
    )
    done = sum(1 for r in rows if r["status"] == "done")
    failed = len(rows) - done
    logger.info("AI 政策解读完成: 处理 %d 天, 成功 %d, 失败 %d, 写入 %d", len(rows), done, failed, written)
    return {"days": len(rows), "done": done, "failed": failed}


async def run_policy_ai_task() -> None:
    """后台任务包装（worker 子进程调用）。"""
    try:
        result = await sync_policy_analysis()
        logger.info("AI 政策解读后台任务完成: %s", result)
    except Exception:
        logger.exception("AI 政策解读后台任务失败")