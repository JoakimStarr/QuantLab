"""政策风向同步：多源政策新闻 → PG news_policy 表。

数据源分层（层级越高越权威，决定权在 AI 解读时区分）：
- cctv  央视新闻联播（akshare news_cctv）—— 国家级定调，最高权重
- cjzc  金十数据·全球财经早餐（xnews.jin10.com 页面抓取）—— 每日综合综述
- em    东方财富·全球财经快讯（akshare stock_info_global_em）—— 市场细节（默认关闭）

定位与宏观管线不同：
- 纯文本数据，**只存库、只展示**，不 forward-fill、不广播写 qlib bin
- 手动触发（与宏观同步惯例一致），cctv 增量 = 库中最后日期+1 → 今天；
  首次同步（库为空）回填近 N 天（默认 365）——央视官网文字稿仅保留有限历史
- cjzc 为快照源（金十每日一篇综述，只抓当日最新一篇）；em 为快照源
  （单次调用返回近期全部），幂等 upsert 即可

news_cctv 内部逐条抓取当天每条新闻的文稿（约 20 次请求/天），
单日抓取需要数秒；多日并发抓取（akshare 不连 baostock，可并行），
失败单日跳过不影响其他。
"""
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from lxml import etree
from sqlalchemy import func, select

from app.core.database import async_session
from app.core.executor import run_io_cpu
from app.models.policy import PolicyNews
from app.services.data.db_utils import bulk_upsert

logger = logging.getLogger(__name__)

# 来源 code → 展示名（后端 API / 前端共用约定；后端是权威来源）
SOURCE_LABELS = {
    "cctv": "新闻联播",
    "cjzc": "金十早餐",
    "em": "东财快讯",
}

# 新闻抓取并发度（akshare news_cctv 单请求独立，可并行；太高易触发接口限流）
_FETCH_WORKERS = 8


def _enabled_sources() -> list[str]:
    """启用的新闻源（config.mining.policy_news.sources，默认 cctv+cjzc）。"""
    default = ["cctv", "cjzc"]
    try:
        from app.core.config import settings
        cfg = (settings.mining.get("policy_news", {}) or {}) or {}
        sources = cfg.get("sources") or default
        return [s for s in sources if s in _FETCHERS]
    except Exception:
        return default


def ai_sources() -> list[str]:
    """送入 AI 解读的来源（config.mining.policy_news.ai_sources，默认 cctv+cjzc）。

    快讯（em）噪声大、token 成本高，默认不送入 AI，仅入库供检索展示。
    """
    default = ["cctv", "cjzc"]
    try:
        from app.core.config import settings
        cfg = (settings.mining.get("policy_news", {}) or {}) or {}
        sources = cfg.get("ai_sources") or default
        return [s for s in sources if s in _FETCHERS]
    except Exception:
        return default


# ==== cctv：按日抓取（支持多日并发） ====

def _fetch_one_day(d: str) -> tuple[str, list[dict]]:
    """抓取单日新闻联播（供线程池调用）。返回 (date_str, rows)。"""
    import akshare as ak

    try:
        df = ak.news_cctv(date=d.replace("-", ""))
    except Exception as e:
        logger.warning("news_cctv 抓取失败 %s: %s", d, e)
        return d, []
    if df is None or df.empty:
        return d, []
    rows = []
    for _, r in df.iterrows():
        title = str(r.get("title") or "").strip()
        if not title:
            continue
        rows.append({
            "news_date": date.fromisoformat(d),
            "title": title,
            "content": str(r.get("content") or "").strip() or None,
            "source": "cctv",
        })
    logger.info("news_cctv %s 抓取 %d 条", d, len(df))
    return d, rows


def fetch_policy_news(days: list[str]) -> list[dict]:
    """并发逐日调用 akshare news_cctv 抓取、归一化为窄表行（同步阻塞，需线程池执行）。

    新闻联播每日必播（非交易日也有），days 覆盖的是自然日。
    """
    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as ex:
        for _d, day_rows in ex.map(_fetch_one_day, days):
            if day_rows:
                rows.extend(day_rows)
    return rows


# ==== cjzc / em：快照源（单次调用返回全部/近期） ====

def _parse_pub_time(value) -> date | None:
    """从发布时间列解析日期（兼容 '2024-03-13 08:00:00' / '2024-03-13T08:00:00' 等）。"""
    if value is None:
        return None
    s = str(value).strip().replace("T", " ")
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


# ==== cjzc：金十数据·全球财经早餐（每日一篇综述，快照源，只抓当日最新一篇） ====

_JIN10_LIST_URL = "https://xnews.jin10.com/30"
_JIN10_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
# 正文中嵌入的多版本音频切换文案（放在 p.insert-audio 内，行级兜底剔除）
_JIN10_AUDIO_TEXTS = frozenset({
    "男生普通话版", "女生普通话版", "女声普通话版", "粤语版", "西南方言版", "东北话版", "上海话版", "下载mp3",
})


def _jin10_list_items() -> list[tuple[str, str]]:
    """金十早餐列表页 → [(href, 链接文本)]（去重保序，早餐链接排最前）。

    「全球财经早餐」是每日一篇综述，位于列表顶部，链接文本形如
    「金十数据全球财经早餐 | 2026年8月18日 …」；其余为当日单条快讯。
    """
    import requests as req

    resp = req.get(_JIN10_LIST_URL, timeout=20, headers=_JIN10_HEADERS)
    resp.raise_for_status()
    html = etree.HTML(resp.text)
    # 同一文章会同时出现空文本与带文本两个 <a>，按 href 去重并保留更完整的文本
    seen: dict[str, str] = {}
    for a in html.xpath("//div[@class='jin10-news-list']//a"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        text = " ".join(a.itertext()).strip()
        if text and len(text) > len(seen.get(href, "")):
            seen[href] = text
    # 早餐链接（文本含「财经早餐」）排最前，其余保持页面顺序
    items = sorted(seen.items(), key=lambda kv: 0 if "财经早餐" in kv[1] else 1)
    return [(href, text) for href, text in items]


def _jin10_breakfast_date(title: str) -> date | None:
    """从早餐标题「金十数据全球财经早餐 | 2026年8月18日」解析日期。"""
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", title)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _jin10_breakfast_content(url: str) -> str | None:
    """抓取单篇早餐正文：段落级提取（h2 小标题各占一行），剔除音频块与文末免责声明。"""
    import requests as req

    resp = req.get(url, timeout=20, headers=_JIN10_HEADERS)
    resp.raise_for_status()
    bodies = etree.HTML(resp.text).xpath("//div[@class='jin10-news-cdetails-content']")
    if not bodies:
        return None
    lines: list[str] = []
    for node in bodies[0].xpath(".//p | .//h2 | .//h3"):
        if "insert-audio" in (node.get("class") or ""):
            continue
        text = " ".join(node.itertext()).strip()
        if not text or text in _JIN10_AUDIO_TEXTS:
            continue
        if text.startswith("风险提示及免责条款"):
            break  # 文末免责声明对政策解读无价值，丢弃其后的所有内容
        lines.append(text)
    return "\n".join(lines).strip() or None


def _fetch_cjzc(days: list[str] | None = None) -> list[dict]:
    """金十数据·全球财经早餐：当日一篇综述（今日优选/市场盘点/国际要闻/国内要闻/风险预警）。

    快照源：每次同步只抓列表里最新一篇早餐，幂等 upsert（(news_date, title) 唯一）。
    """
    rows: list[dict] = []
    for href, text in _jin10_list_items():
        title = (text.splitlines()[0] if text else "").strip()
        if "财经早餐" not in title:
            continue  # 列表首个不是早餐（页面改版等异常），继续找下一条
        d = _jin10_breakfast_date(title)
        if d is None:
            logger.warning("金十早餐标题未含有效日期，跳过: %s", title)
            continue
        content = _jin10_breakfast_content(href)
        if not content:
            logger.warning("金十早餐正文抓取失败: %s", href)
            continue
        rows.append({
            "news_date": d,
            "title": title[:500],
            "content": content,
            "source": "cjzc",
        })
        logger.info("金十早餐抓取成功: %s（%d 字符）", title, len(content))
        break  # 只取当日（最新一篇）早餐
    return rows


def _fetch_em(days: list[str] | None = None) -> list[dict]:
    """东财全球财经快讯：单次调用返回最近 200 条（标题/摘要/发布时间/链接）。"""
    import akshare as ak

    try:
        df = ak.stock_info_global_em()
    except Exception as e:
        logger.warning("stock_info_global_em 抓取失败: %s", e)
        return []
    if df is None or df.empty:
        return []
    rows = []
    for _, r in df.iterrows():
        title = str(r.get("标题") or "").strip()
        if not title:
            continue
        d = _parse_pub_time(r.get("发布时间"))
        if d is None:
            continue
        rows.append({
            "news_date": d,
            "title": title[:500],
            "content": str(r.get("摘要") or "").strip() or None,
            "source": "em",
        })
    logger.info("stock_info_global_em 抓取 %d 条", len(df))
    return rows


_FETCHERS = {
    "cctv": fetch_policy_news,
    "cjzc": _fetch_cjzc,
    "em": _fetch_em,
}


async def _max_policy_date() -> date | None:
    async with async_session() as session:
        r = await session.execute(select(func.max(PolicyNews.news_date)))
        return r.scalar()


async def upsert_policy(rows: list[dict]) -> int:
    """幂等写入（(news_date, title) 冲突跳过）。"""
    return await bulk_upsert(PolicyNews, rows, ["news_date", "title"], batch=500)


async def sync_policy_news(backfill_days: int = 365) -> tuple[int, int]:
    """增量同步多源政策新闻。

    cctv：增量（库中最后日期+1 → 今天；库空回填 backfill_days 天）。
    cjzc：金十早餐快照源，只抓当日最新一篇；em：快照源，单次抓取近期全部。幂等 upsert。

    Returns: (新增条数, 拉取覆盖天数)
    """
    sources = _enabled_sources()
    max_d = await _max_policy_date()
    today = date.today()
    total_inserted = 0
    days_covered = 0

    for source in sources:
        fetcher = _FETCHERS[source]
        if source == "cctv":
            if max_d:
                start = max_d + timedelta(days=1)
            else:
                start = today - timedelta(days=backfill_days)
            if start > today:
                rows: list[dict] = []
            else:
                days = [start + timedelta(days=i) for i in range((today - start).days + 1)]
                rows = await run_io_cpu(fetcher, [d.isoformat() for d in days])
                days_covered += len(days)
        else:
            rows = await run_io_cpu(fetcher, [])
        if rows:
            total_inserted += await upsert_policy(rows)

    logger.info("政策风向同步完成: 源=%s, 覆盖 %d 天, 新增 %d 条",
                sources, days_covered, total_inserted)
    return total_inserted, days_covered


async def run_policy_sync_task() -> None:
    """后台任务包装：同步政策新闻并记录日志（worker 子进程调用）。"""
    try:
        inserted, days = await sync_policy_news()
        logger.info("政策风向后台同步完成: 覆盖 %d 天, 新增 %d 条", days, inserted)
    except Exception:
        logger.exception("政策风向后台同步失败")
