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
from app.services.data import policy_ai_progress
from app.services.data.db_utils import bulk_upsert

logger = logging.getLogger(__name__)

AI_BACKFILL_DAYS = 30        # 默认回填窗口（天）
# 每条新闻内容截断长度（按来源区分：cctv 定调摘要 400 字足够，cjzc 金十早餐综述约 2500 字）
_MAX_CONTENT_CHARS = {"cctv": 400, "cjzc": 3000, "em": 300}
_DEFAULT_CONTENT_CHARS = 400
_MAX_ITEMS = 24              # 每天最多送入 LLM 的新闻联播条数
_MAX_EM_ITEMS = 5            # 每天最多送入 LLM 的东财快讯条数（默认不进 AI，见 ai_sources）
_CONCURRENCY = 4             # LLM 并发数（默认 4；可用 config.mining.policy_ai.concurrency 覆盖）
_MAX_RETRY = 3               # 失败日期最大重试次数（超过后不再自动重试，避免每次同步反复打失败日）


def _concurrency() -> int:
    """AI 解读并发数：优先读 config（mining.policy_ai.concurrency），默认 _CONCURRENCY。"""
    try:
        from app.core.config import settings
        return int(settings.mining.get("policy_ai", {}).get("concurrency", _CONCURRENCY))
    except Exception:
        return _CONCURRENCY


def _max_retry() -> int:
    """失败日期最大重试次数：优先读 config（mining.policy_ai.max_retry），默认 _MAX_RETRY。"""
    try:
        from app.core.config import settings
        return int(settings.mining.get("policy_ai", {}).get("max_retry", _MAX_RETRY))
    except Exception:
        return _MAX_RETRY

SYSTEM_PROMPT = """你是 A 股的宏观政策研究员。用户会给你某一天的政策信息（央视《新闻联播》文字稿、金十数据财经早餐综述等），每条标注来源。
来源权重：新闻联播(cctv) 是国家级定调，权重最高，是解读主依据；财经早餐(cjzc) 是每日综合综述，作参考；快讯(em) 仅作细节补充，忽略纯市场/公司新闻。
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
要求：只依据给定材料，不编造；sectors 必须给出 direction；topics 的 score 为 0~1 小数，
表示当日该主题的政策力度与市场相关度。"""

_USER_TEMPLATE = """日期：{day}（{weekday}）

政策信息：
{items}
"""


def _build_messages(day: date, news_rows: list[dict]) -> list[dict]:
    """组装 messages：截断长文，控制 token 预算；每条标注来源权重。"""
    from app.services.data.policy_sync import SOURCE_LABELS

    lines = []
    for i, r in enumerate(news_rows, 1):
        title = r["title"]
        source = r.get("source") or "cctv"
        label = SOURCE_LABELS.get(source, source)
        content = r.get("content") or ""
        cap = _MAX_CONTENT_CHARS.get(source, _DEFAULT_CONTENT_CHARS)
        if len(content) > cap:
            content = content[:cap] + "…"
        lines.append(f"{i}. 【{label}】{title}: {content}")
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
    """取某一天的多源政策新闻（按来源分层，控制 token 预算）。

    顺序：cctv（定调，主依据）→ cjzc（综述）→ em（细节）。仅取 ai_sources 启用的来源。
    """
    from app.services.data.policy_sync import ai_sources

    async with async_session() as session:
        rows = (await session.execute(
            select(PolicyNews.title, PolicyNews.content, PolicyNews.source)
            .where(PolicyNews.news_date == news_date)
            .order_by(PolicyNews.id)
        )).all()

    enabled = set(ai_sources())
    buckets: dict[str, list[dict]] = {"cctv": [], "cjzc": [], "em": []}
    for title, content, source in rows:
        source = source or "cctv"
        if source not in enabled:
            continue
        buckets.setdefault(source, []).append({"title": title, "content": content, "source": source})

    items = []
    items.extend(buckets["cctv"][:_MAX_ITEMS])
    items.extend(buckets["cjzc"][:1])
    items.extend(buckets["em"][:_MAX_EM_ITEMS])
    return items


async def analyze_one_day(news_date: date) -> dict:
    """对单日文字稿调用 LLM 产出解读（网络 IO）。"""
    from app.services.ai.llm_json import call_llm_json

    items = await _day_news(news_date)
    if not items:
        raise ValueError(f"{news_date} 无新闻数据")
    result = await call_llm_json(_build_messages(news_date, items))
    return _normalize(result)


async def _pending_dates(max_days: int) -> list[date]:
    """最近 max_days 内「有新闻但无 done 解读」的日期（新→旧）。

    失败重试限制：failed 且 retry_count >= _MAX_RETRY 的日期不再重试
    （LLM 持续失败时避免每次同步都重跑同样的失败日）。
    """
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
        # failed 但重试次数已耗尽 → 视为完成（不再重试）
        exhausted = set((await session.execute(
            select(PolicyAnalysis.news_date).where(
                PolicyAnalysis.news_date >= start,
                PolicyAnalysis.status == "failed",
                PolicyAnalysis.retry_count >= _max_retry(),
            )
        )).scalars())
    return sorted(news_dates - done_dates - exhausted, reverse=True)


async def sync_policy_analysis(backfill_days: int = AI_BACKFILL_DAYS,
                               progress_cb=None) -> dict:
    """对「有新闻但无解读」的日期调用 LLM 补齐解读。

    边跑边写：每个日期处理完立即 upsert，中途进程重启也不丢已完成部分，
    前端可实时看到进度（policy/status 的 ai_done/ai_pending 计数）。

    Args:
        backfill_days: 回填窗口（天）
        progress_cb: 可选 progress_cb(idx, total, date_str)
    Returns: {"days": 处理天数, "done": 成功数, "failed": 失败数}
    """
    pending = await _pending_dates(backfill_days)
    if not pending:
        policy_ai_progress.finish(True, total=0)
        return {"days": 0, "done": 0, "failed": 0}
    logger.info("AI 政策解读待处理 %d 天: %s ... %s", len(pending), pending[-1], pending[0])
    policy_ai_progress.start(len(pending))

    sem = asyncio.Semaphore(_concurrency())

    async def process(d: date) -> dict:
        async with sem:
            # 读取该日已有的重试次数（若此前 failed）
            try:
                async with async_session() as session:
                    prev = (await session.execute(
                        select(PolicyAnalysis.retry_count).where(PolicyAnalysis.news_date == d)
                    )).scalar()
            except Exception:
                prev = None
            retry = (prev or 0) + 1
            try:
                parsed = await analyze_one_day(d)
                return {"news_date": d, "status": "done", **parsed, "error": None,
                        "retry_count": retry}
            except Exception as e:
                logger.warning("AI 解读 %s 失败(第%d次): %s", d, retry, e)
                return {"news_date": d, "status": "failed", "summary": None,
                        "policy_tone": None, "key_items": None, "sectors": None,
                        "topics": None, "keywords": None, "market_impact": None,
                        "error": str(e)[:500], "retry_count": retry}

    done = 0
    failed = 0
    try:
        for i, d in enumerate(pending):
            if progress_cb:
                progress_cb(i + 1, len(pending), d.isoformat())
            row = await process(d)
            # 单日即时 upsert：失败日期写 failed 供下次重试，成功日期立即可查
            await bulk_upsert(
                PolicyAnalysis, [row], ["news_date"], batch=50,
                update_cols=["status", "summary", "policy_tone", "key_items", "sectors",
                             "topics", "keywords", "market_impact", "error", "retry_count"],
            )
            if row["status"] == "done":
                done += 1
            else:
                failed += 1
            policy_ai_progress.update(done, failed, len(pending))
            if (i + 1) % 20 == 0:
                logger.info("AI 政策解读进度 %d/%d (done=%d failed=%d)",
                            i + 1, len(pending), done, failed)
    except Exception:
        policy_ai_progress.finish(False, error="policy_ai 任务异常退出")
        raise

    policy_ai_progress.finish(True, done=done, failed=failed, total=len(pending))
    logger.info("AI 政策解读完成: 处理 %d 天, 成功 %d, 失败 %d",
                len(pending), done, failed)
    return {"days": len(pending), "done": done, "failed": failed}


async def run_policy_ai_task(backfill_days: int = AI_BACKFILL_DAYS) -> None:
    """后台任务包装（worker 子进程调用）。

    Args:
        backfill_days: AI 解读回填窗口（天），透传 API 的 backfill_days 参数。
    """
    try:
        result = await sync_policy_analysis(backfill_days=backfill_days)
        logger.info("AI 政策解读后台任务完成: %s", result)
    except Exception:
        logger.exception("AI 政策解读后台任务失败")
