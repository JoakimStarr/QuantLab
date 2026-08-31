"""每日晨报 / 盘前简报：聚合政策定调、外盘隔夜、宏观快照、市场概况，+ LLM 综合研判。

结构化各板块来自现成数据源（无新增数据管线）；LLM 只负责一段「综合研判/关注板块/
风险提示/今日展望」，失败时降级为纯结构化展示（llm_status='degraded'），不影响其他板块。

落库：daily_report 表按 report_date 唯一，幂等 upsert（bulk_upsert），已生成直接返回缓存。
并发：data/daily_report.lock（flock）防止重复触发 LLM 造成成本浪费；DB 唯一键兜底。
"""
import asyncio
import json
import logging
from datetime import date

from sqlalchemy import select

from app.core.database import async_session
from app.models.daily_report import DailyReport
from app.models.policy import PolicyAnalysis
from app.services.data.db_utils import bulk_upsert

logger = logging.getLogger(__name__)

DISCLAIMER = "AI 辅助生成，仅供参考，不构成投资建议"


class DailyReportBusyError(Exception):
    """同一报告日期正在生成中（flock 被占用）。"""


def _lock():
    from app.services.data.sync_lock import SyncLock

    try:
        from app.core.config import settings
        path = str(settings.PROJECT_ROOT / "data" / "daily_report.lock")
    except Exception:
        path = None
    return SyncLock(path=path)


SYSTEM_PROMPT = """你是 A 股量化研究员的晨报撰写助手。用户会给你某天的结构化市场数据（政策定调/外盘隔夜/宏观指标/主要指数），
请输出 JSON（不要输出 JSON 之外的任何内容），结构：
{
  "synthesis": "150字以内综合研判 markdown（覆盖政策定调、外盘情绪、宏观与指数格局，给出当日关注方向）",
  "focus_sectors": [{"name": "关注板块/题材", "direction": "利好/利空/中性", "reason": "理由（30字内）"}],
  "risk_notes": ["1-3条风险提示，每条30字内"],
  "outlook": "对今日 A 股市场的一句话展望（50字内）"
}
要求：只依据给定材料，不编造；某板块数据缺失时在 synthesis 中注明"该板块暂无数据"，不要臆造数值。"""


# ---------------- 数据源采集 ----------------

async def _fetch_policy(report_date: date) -> dict | None:
    """该日期的政策 AI 解读（最新一条 done 分析）。"""
    async with async_session() as session:
        r = (await session.execute(
            select(PolicyAnalysis)
            .where(PolicyAnalysis.news_date == report_date, PolicyAnalysis.status == "done")
        )).scalar_one_or_none()
    if r is None:
        return None
    return {
        "policy_tone": r.policy_tone,
        "summary": r.summary,
        "key_items": r.key_items,
        "sectors": r.sectors,
        "topics": r.topics,
        "market_impact": r.market_impact,
    }


async def _fetch_external() -> dict | None:
    """外盘隔夜情绪（同步读 state JSON，用线程避免阻塞事件循环）。"""
    from app.services.data.external_market import get_external_market_state

    state = await asyncio.to_thread(get_external_market_state)
    if not state.get("items"):
        return None
    return {
        "synced_at": state.get("synced_at"),
        "items": state.get("items") or {},
    }


async def _fetch_macro() -> list | None:
    """宏观指标快照（最新值 + 前值）。"""
    from app.services.data.macro_snapshot import get_macro_snapshot

    async with async_session() as session:
        items = await get_macro_snapshot(session)
    return items or None


async def _fetch_market() -> list | None:
    """主要指数概况（读 qlib bin，内部走 executor）。"""
    from app.api.market import market_overview

    resp = await market_overview()
    if not getattr(resp, "ok", False):
        return None
    data = resp.data or {}
    items = data.get("items") or []
    return items or None


async def _collect_sources(report_date: date) -> dict:
    """并行采集四源，单点失败置 None 不阻断整体。"""
    results = await asyncio.gather(
        _fetch_policy(report_date),
        _fetch_external(),
        _fetch_macro(),
        _fetch_market(),
        return_exceptions=True,
    )
    keys = ["policy", "external", "macro", "market"]
    sources = {}
    for k, res in zip(keys, results, strict=True):
        if isinstance(res, Exception):
            logger.warning("晨报采集 %s 失败: %s", k, res)
            sources[k] = None
        else:
            sources[k] = res
    return sources


def _all_empty(sources: dict) -> bool:
    return all(not v for v in sources.values())


# ---------------- LLM ----------------

def _build_llm_messages(report_date: date, sources: dict) -> list[dict]:
    payload = {
        "report_date": report_date.isoformat(),
        "policy": sources.get("policy"),
        "external": sources.get("external"),
        "macro": sources.get("macro"),
        "market": sources.get("market"),
    }
    user = (
        f"日期：{report_date.isoformat()}\n\n"
        f"结构化市场数据：\n{json.dumps(payload, ensure_ascii=False, default=str)}"
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def _normalize_llm(d: dict) -> dict:
    """清洗 LLM 输出：容错缺失字段（仿 policy_ai._normalize）。"""
    ok_list = lambda v: v if isinstance(v, list) else None  # noqa: E731
    return {
        "synthesis": str(d.get("synthesis") or "").strip() or None,
        "focus_sectors": ok_list(d.get("focus_sectors")),
        "risk_notes": ok_list(d.get("risk_notes")),
        "outlook": str(d.get("outlook") or "").strip() or None,
    }


async def _call_llm(report_date: date, sources: dict) -> dict:
    from app.services.ai.llm_json import call_llm_json

    result = await call_llm_json(_build_llm_messages(report_date, sources))
    return _normalize_llm(result)


# ---------------- 主流程 ----------------

async def resolve_anchor(report_date: date | None) -> date:
    """报告锚点日期：未指定时取最新政策解读日期；无则用今天。"""
    if report_date is not None:
        return report_date
    async with async_session() as session:
        latest = (await session.execute(
            select(PolicyAnalysis.news_date)
            .where(PolicyAnalysis.status == "done")
            .order_by(PolicyAnalysis.news_date.desc())
            .limit(1)
        )).scalar()
    return latest or date.today()


async def generate_daily_report(report_date: date | None = None, force: bool = False) -> dict:
    """生成（或取缓存）某一天的晨报。

    - 已有 done 记录且非 force → 直接返回缓存（幂等）
    - 否则重新采集 + 调 LLM（失败降级）→ 幂等 upsert
    并发：flock 抢不到抛 DailyReportBusyError（API 转 409）。
    """
    anchor = await resolve_anchor(report_date)

    lock = _lock()
    if not lock.try_acquire():
        raise DailyReportBusyError(f"{anchor} 晨报正在生成中，请稍后再试")

    try:
        # 缓存检查放在持锁内，避免两个并发请求同时进入生成
        if not force:
            cached = await _find_report(anchor)
            if cached is not None and cached["status"] == "done":
                return cached

        sources = await _collect_sources(anchor)
        if _all_empty(sources):
            row = {
                "report_date": anchor, "status": "failed", "sections": None,
                "synthesis": None, "focus_sectors": None, "risk_notes": None,
                "outlook": None, "llm_status": None,
                "error": "晨报数据源全部为空（无政策/外盘/宏观/指数数据）",
            }
            await bulk_upsert(DailyReport, [row], ["report_date"],
                              update_cols=_UPDATE_COLS)
            return (await _find_report(anchor)) or {}

        llm = None
        llm_status = None
        error = None
        try:
            llm = await _call_llm(anchor, sources)
            llm_status = "ok" if llm.get("synthesis") else "degraded"
            if not llm.get("synthesis"):
                error = "LLM 返回内容无效（无 synthesis）"
        except Exception as e:  # noqa: BLE001
            logger.warning("晨报 LLM 研判失败(降级为纯结构化): %s", e)
            llm_status = "degraded"
            error = str(e)[:500]

        row = {
            "report_date": anchor, "status": "done",
            "sections": {
                "policy": sources.get("policy"),
                "external": sources.get("external"),
                "macro": sources.get("macro"),
                "market": sources.get("market"),
            },
            "synthesis": (llm or {}).get("synthesis"),
            "focus_sectors": (llm or {}).get("focus_sectors"),
            "risk_notes": (llm or {}).get("risk_notes"),
            "outlook": (llm or {}).get("outlook"),
            "llm_status": llm_status,
            "error": error,
        }
        await bulk_upsert(DailyReport, [row], ["report_date"], update_cols=_UPDATE_COLS)
        logger.info("晨报生成完成 %s llm_status=%s", anchor, llm_status)
        return (await _find_report(anchor)) or {}
    finally:
        lock.release()


_UPDATE_COLS = ["status", "sections", "synthesis", "focus_sectors", "risk_notes",
                "outlook", "llm_status", "error"]


async def _find_report(report_date: date) -> dict | None:
    async with async_session() as session:
        row = (await session.execute(
            select(DailyReport).where(DailyReport.report_date == report_date)
        )).scalar_one_or_none()
    return _row_to_dict(row) if row else None


def _row_to_dict(row: DailyReport) -> dict:
    return {
        "report_date": row.report_date.isoformat(),
        "status": row.status,
        "sections": row.sections,
        "synthesis": row.synthesis,
        "focus_sectors": row.focus_sectors,
        "risk_notes": row.risk_notes,
        "outlook": row.outlook,
        "llm_status": row.llm_status,
        "error": row.error,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def get_report(report_date: date | None = None) -> dict | None:
    """取单日晨报；未指定返回最新一条。"""
    if report_date is not None:
        return await _find_report(report_date)
    async with async_session() as session:
        row = (await session.execute(
            select(DailyReport).order_by(DailyReport.report_date.desc()).limit(1)
        )).scalar_one_or_none()
    return _row_to_dict(row) if row else None


async def list_reports(limit: int = 30, offset: int = 0) -> list[dict]:
    """历史列表（不含大字段，避免载荷膨胀）。"""
    async with async_session() as session:
        rows = (await session.execute(
            select(DailyReport)
            .order_by(DailyReport.report_date.desc())
            .offset(offset).limit(limit)
        )).scalars().all()
    return [{
        "report_date": r.report_date.isoformat(),
        "status": r.status,
        "llm_status": r.llm_status,
        "error": r.error,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    } for r in rows]
