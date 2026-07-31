"""③ 文本因子挖掘：从新闻/公告提取情绪信号构造因子。

复用 data/news_service（或 akshare stock_news_em）拉取新闻，
LLM 批量情绪分类，聚合为每日截面情绪因子，评价 IC 后入库。
"""
import json
import logging
import asyncio
from datetime import datetime, timedelta
import pandas as pd
from app.core.database import async_session
from app.core.config import settings
from app.models.mining_task import MiningTask
from app.services.factor.library import add_factor, update_factor_metrics
from app.services.mining.task_utils import update_task_status as _update_task

logger = logging.getLogger(__name__)

# 情绪分类 prompt：包含日期，要求 LLM 返回 date 字段，保留时间维度以便计算 IC
# 注意：所有字面花括号必须用 {{ }} 转义，因为 .format() 会解析 {news}
_SENT_PROMPT = """对以下股票新闻进行情绪分类，返回 JSON 对象 {{"results": [...]}}。
每条: {{"code": "股票代码", "date": "YYYY-MM-DD", "score": 1或0或-1, "reason": "简短原因"}}
1=利好, 0=中性, -1=利空。只返回 JSON 对象，不要额外文字。

新闻列表：
{news}
"""


def _normalize_code(code: str) -> str:
    """归一化股票代码为 6 位数字（去除可能的交易所前缀如 sh/sz/bj）。"""
    code = str(code).strip().lower()
    for prefix in ("sh", "sz", "bj"):
        if code.startswith(prefix):
            code = code[len(prefix):]
            break
    return code.strip().zfill(6)


async def _classify_sentiment(news_items: list[dict]) -> list[dict]:
    """调用 LLM 批量情绪分类。

    news_items: [{"code": "600000", "title": "...", "date": "2026-07-28"}, ...]
    返回: [{"code": "600000", "date": "2026-07-28", "score": 1, "reason": "..."}, ...]
    """
    from app.services.ai.provider_router import ProviderRouter
    if not news_items:
        return []
    results = []
    router = ProviderRouter()
    batch_size = 20
    for i in range(0, len(news_items), batch_size):
        batch = news_items[i:i + batch_size]
        # 输入包含日期，LLM 需在响应中回传
        news_text = "\n".join(
            f"{b.get('code','')} | {b.get('date','')} | {b.get('title','')}"
            for b in batch
        )
        messages = [{"role": "user", "content": _SENT_PROMPT.format(news=news_text)}]
        try:
            res = await router.route_request(messages)
            content = res["content"]
            # LLM 可能返回 {"results": [...]} 或直接返回 [...]
            if isinstance(content, str):
                content = json.loads(content)
            if isinstance(content, dict):
                # 提取 results 字段（或其他列表字段）
                items = None
                for key in ("results", "data", "items", "list"):
                    if key in content and isinstance(content[key], list):
                        items = content[key]
                        break
                if items is None:
                    # 整个 dict 当作单条结果
                    items = [content]
            elif isinstance(content, list):
                items = content
            else:
                items = []
            results.extend(items)
        except Exception as e:
            logger.warning("情绪分类批次失败: %s", e)
    return results


async def _fetch_news_for_universe(codes: list[str], days: int = 30) -> list[dict]:
    """拉取 universe 股票近 days 天新闻。"""
    from app.services.data.akshare_client import fetch_data, get_stock_news
    items = []
    # 限频，串行拉取前 N 只
    limit = min(len(codes), settings.mining.get("text", {}).get("max_news_per_day", 50))
    for code in codes[:limit]:
        try:
            df = await fetch_data(get_stock_news, symbol=code, timeout=15)
            if df is None or df.empty:
                continue
            # 列：关键词, 新闻标题, 新闻内容, 发布时间, 文章来源, 新闻链接
            for _, row in df.head(10).iterrows():
                title = row.get("新闻标题", "")
                date_str = str(row.get("发布时间", ""))[:10]  # YYYY-MM-DD
                items.append({
                    "code": _normalize_code(code),
                    "title": str(title),
                    "date": date_str,
                })
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.debug("新闻拉取失败 %s: %s", code, e)
    return items


async def mine_with_text(task_id: int, codes: list[str] = None) -> dict:
    """文本因子挖掘主流程。"""
    from app.services.quant.qlib_init import init_qlib
    await _update_task(task_id, status="running", started_at=datetime.now())
    try:
        init_qlib()
        # 文本因子关注近期新闻情绪，日期范围用近期（而非回测配置的 2020-2024）
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

        if codes is None:
            from app.services.quant.data_adapter import get_universe
            codes = await get_universe()
            codes = codes[:30]  # 控制规模

        # 保留原始 params 中的 codes，追加 n_codes 摘要
        async with async_session() as session:
            r = await session.get(MiningTask, task_id)
            existing_params = json.loads(r.params) if r and r.params else {}
            existing_params["n_codes"] = len(codes)
            await session.commit()
        await _update_task(task_id, params=json.dumps(existing_params))
        # 拉取新闻 + 情绪分类
        news = await _fetch_news_for_universe(codes)
        await _update_task(task_id, candidates_generated=len(news))

        if not news:
            raise ValueError("未拉取到任何新闻数据")

        sentiments = await _classify_sentiment(news)
        if not sentiments:
            raise ValueError("LLM 情绪分类返回空结果")

        # 聚合为每日截面情绪: (date, code) -> mean score
        rows = []
        for s in sentiments:
            code = _normalize_code(s.get("code", ""))
            score = s.get("score")
            if not code or score is None:
                continue
            try:
                score = float(score)
            except (TypeError, ValueError):
                continue
            # 日期：优先用 LLM 返回的，否则用今天
            date = s.get("date") or datetime.now().strftime("%Y-%m-%d")
            # 截取前 10 字符防止 LLM 返回带时间的日期
            date = str(date)[:10]
            rows.append({"date": date, "code": code, "score": score})
        if not rows:
            raise ValueError("情绪数据解析为空")

        sent_df = pd.DataFrame(rows)
        sent_df["date"] = pd.to_datetime(sent_df["date"])
        daily = sent_df.groupby(["date", "code"])["score"].mean().reset_index()

        # 转 qlib MultiIndex 格式评价 IC
        # 注意：qlib 内部 instrument 用大写（SH600000），但 to_qlib_code 返回小写（文件系统用），
        # 需 .upper() 转为大写以匹配 qlib 数据
        from app.services.data.code_utils import to_qlib_code
        daily["qlib_code"] = daily["code"].apply(lambda c: to_qlib_code(c).upper())
        daily = daily.set_index(["date", "qlib_code"])[["score"]].rename(columns={"score": "factor"})
        daily.index.names = ["datetime", "instrument"]

        # 加载标签并计算 IC
        from app.services.quant.factor_eval import load_label, compute_ic
        label_df = await asyncio.get_running_loop().run_in_executor(
            None, load_label, start, end, None
        )
        ic_metrics = compute_ic(daily, label_df)

        ic = ic_metrics.get("ic")
        ic_threshold = settings.mining.get("text", {}).get("ic_threshold",
                        settings.mining.get("llm", {}).get("ic_threshold", 0.03))
        passed = ic is not None and abs(ic) >= ic_threshold

        factor_id = None
        if passed:
            factor = await add_factor(
                name=f"text_sentiment_{task_id}",
                expression="TextSentiment(news)",
                category="text",
                description=f"新闻情绪因子(task={task_id}, 样本={len(rows)})",
                source_task_id=task_id, skip_validation=True,
            )
            await update_factor_metrics(factor["id"], ic_metrics)
            factor_id = factor["id"]

        await _update_task(
            task_id, status="done",
            candidates_generated=len(sentiments),
            candidates_passed=1 if passed else 0,
            best_ic=ic or 0.0,
            result_factor_ids=json.dumps([factor_id] if factor_id else []),
            finished_at=datetime.now(),
        )
        return {"task_id": task_id, "news_count": len(news),
                "sentiment_count": len(sentiments), "ic": ic,
                "passed": passed, "factor_id": factor_id,
                "ic_metrics": ic_metrics}
    except Exception as e:
        await _update_task(task_id, status="failed", error=str(e)[:500],
                           finished_at=datetime.now())
        raise

