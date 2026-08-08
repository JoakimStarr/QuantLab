"""政策风向同步：央视新闻联播文字稿（akshare news_cctv）→ PG news_policy 表。

定位与宏观管线不同：
- 纯文本数据，**只存库、只展示**，不 forward-fill、不广播写 qlib bin
- 手动触发（与宏观同步惯例一致），增量 = 库中最后日期+1 → 今天；
  首次同步（库为空）回填近 N 天（默认 365）——央视官网文字稿仅保留有限历史

news_cctv 内部逐条抓取当天每条新闻的文稿（约 20 次请求/天），
单日抓取需要数秒；多日并发抓取（akshare 不连 baostock，可并行），
失败单日跳过不影响其他。
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from sqlalchemy import func, select

from app.core.database import async_session
from app.core.executor import run_io_cpu
from app.models.policy import PolicyNews
from app.services.data.db_utils import bulk_upsert

logger = logging.getLogger(__name__)

SOURCE = "cctv"

# 新闻抓取并发度（akshare news_cctv 单请求独立，可并行；太高易触发接口限流）
_FETCH_WORKERS = 8


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
            "source": SOURCE,
        })
    logger.info("news_cctv %s 抓取 %d 条", d, len(df))
    return d, rows


def fetch_policy_news(days: list[str]) -> list[dict]:
    """并发逐日调用 akshare news_cctv 抓取、归一化为窄表行（同步阻塞，需线程池执行）。

    新闻联播每日必播（非交易日也有），days 覆盖的是自然日。
    """
    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as ex:
        for d, day_rows in ex.map(_fetch_one_day, days):
            if day_rows:
                rows.extend(day_rows)
    return rows


async def _max_policy_date() -> date | None:
    async with async_session() as session:
        r = await session.execute(select(func.max(PolicyNews.news_date)))
        return r.scalar()


async def upsert_policy(rows: list[dict]) -> int:
    """幂等写入（(news_date, title) 冲突跳过）。"""
    return await bulk_upsert(PolicyNews, rows, ["news_date", "title"], batch=500)


async def sync_policy_news(backfill_days: int = 365) -> tuple[int, int]:
    """增量同步新闻联播（库中最后日期 → 今天；库空时回填 backfill_days 天）。

    Returns: (新增条数, 拉取覆盖天数)
    """
    max_d = await _max_policy_date()
    today = date.today()
    if max_d:
        start = max_d + timedelta(days=1)
    else:
        start = today - timedelta(days=backfill_days)
    if start > today:
        return 0, 0
    days = [start + timedelta(days=i) for i in range((today - start).days + 1)]
    rows = await run_io_cpu(fetch_policy_news, [d.isoformat() for d in days])
    inserted = await upsert_policy(rows)
    logger.info("政策风向同步完成: 拉取 %d 天, 新增 %d 条", len(days), inserted)
    return inserted, len(days)


async def run_policy_sync_task() -> None:
    """后台任务包装：同步新闻联播并记录日志（worker 子进程调用）。"""
    try:
        inserted, days = await sync_policy_news()
        logger.info("政策风向后台同步完成: 覆盖 %d 天, 新增 %d 条", days, inserted)
    except Exception:
        logger.exception("政策风向后台同步失败")