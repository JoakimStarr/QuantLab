"""③ 文本因子挖掘：从新闻/公告提取情绪信号构造因子。

复用 data/news_service（或 akshare stock_news_em）拉取新闻，
LLM 批量情绪分类，聚合为每日截面情绪因子，评价 IC 后入库。
"""
import json
import logging
import asyncio
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from app.core.database import async_session
from app.core.config import settings
from app.models.mining_task import MiningTask
from app.services.factor.library import add_factor, update_factor_metrics

logger = logging.getLogger(__name__)

_SENT_PROMPT = """对以下股票新闻进行情绪分类，返回 JSON 数组。
每条: {{"code": "股票代码", "score": 1或0或-1, "reason": "简短原因"}}
1=利好, 0=中性, -1=利空。只返回 JSON，不要额外文字。

新闻列表：
{news}
"""


async def _classify_sentiment(news_items: list[dict]) -> list[dict]:
    """调用 LLM 批量情绪分类。"""
    from app.services.ai.provider_router import ProviderRouter
    if not news_items:
        return []
    # 分批（每批最多 20 条，控制 token）
    results = []
    router = ProviderRouter()
    batch_size = 20
    for i in range(0, len(news_items), batch_size):
        batch = news_items[i:i + batch_size]
        news_text = "\n".join(f"{b.get('code','')}: {b.get('title','')}" for b in batch)
        messages = [{"role": "user", "content": _SENT_PROMPT.format(news=news_text)}]
        try:
            res = await router.route_request(messages)
            content = res["content"]
            if isinstance(content, str):
                content = json.loads(content)
            if isinstance(content, list):
                results.extend(content)
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
            # 列：标题, 内容, 发布时间, 文章来源...
            for _, row in df.head(10).iterrows():
                items.append({
                    "code": code,
                    "title": str(row.get("新闻标题", row.iloc[0] if len(row) else "")),
                    "date": str(row.get("发布时间", ""))[:10],
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
        period = settings.quant.get("default_backtest_period", {})
        end = period.get("end", datetime.now().strftime("%Y-%m-%d"))
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
        sentiments = await _classify_sentiment(news)
        await _update_task(task_id, candidates_generated=len(sentiments))

        if not sentiments:
            raise ValueError("未获取到有效情绪数据")

        # 聚合为每日截面情绪: (date, code) -> mean score
        rows = []
        for s in sentiments:
            code = str(s.get("code", "")).strip().zfill(6)
            score = s.get("score")
            if not code or score is None:
                continue
            try:
                score = float(score)
            except (TypeError, ValueError):
                continue
            # 关联日期（用新闻日期，缺省用今天）
            date = s.get("date") or datetime.now().strftime("%Y-%m-%d")
            rows.append({"date": date, "code": code, "score": score})
        if not rows:
            raise ValueError("情绪数据解析为空")

        sent_df = pd.DataFrame(rows)
        sent_df["date"] = pd.to_datetime(sent_df["date"])
        daily = sent_df.groupby(["date", "code"])["score"].mean().reset_index()

        # 转 qlib MultiIndex 格式评价 IC
        from app.services.quant.data_adapter import _to_qlib_code
        daily["qlib_code"] = daily["code"].apply(_to_qlib_code)
        daily = daily.set_index(["date", "qlib_code"])[["score"]].rename(columns={"score": "factor"})
        daily.index.names = ["datetime", "instrument"]

        # 加载标签并计算 IC
        from app.services.quant.factor_eval import load_label, compute_ic
        label_df = await asyncio.get_running_loop().run_in_executor(
            None, load_label, start, end, None
        )
        ic_metrics = compute_ic(daily, label_df)

        ic = ic_metrics.get("ic")
        ic_threshold = settings.mining.get("llm", {}).get("ic_threshold", 0.03)
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
                "passed": passed, "factor_id": factor_id}
    except Exception as e:
        await _update_task(task_id, status="failed", error=str(e)[:500],
                           finished_at=datetime.now())
        raise


async def _update_task(task_id: int, **kwargs):
    from app.services.mining.task_utils import update_task_status
    await update_task_status(task_id, **kwargs)
