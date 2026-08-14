"""经典策略回测历史存取 + 与规则历史合并查询（对比/聚合）。

每次 /classic-strategies/backtest 自动保存一条（配置 + 结果快照）。
列表返回轻量摘要，详情返回全量。提供 `list_combined` 把经典历史与
RuleBacktestHistory 合并（按 created_at 倒序），供策略库页面统一展示、
勾选对比与性能聚合。
"""
import json
import logging

from sqlalchemy import func, select

from app.core.database import async_session
from app.models.classic_backtest_history import ClassicBacktestHistory
from app.models.rule_backtest_history import RuleBacktestHistory
from app.services.strategy_rule_history import _base as _rule_base, _detail as _rule_detail

logger = logging.getLogger(__name__)


async def save_classic_history(result: dict) -> int | None:
    """把 run_classic_strategy 的返回结果落库，返回新 id；异常不阻断回测。"""
    try:
        async with async_session() as session:
            row = ClassicBacktestHistory(
                strategy_key=result.get("key") or "",
                strategy_name=result.get("name") or "",
                category=result.get("category"),
                is_factor=result.get("kind") == "factor",
                params=json.dumps(result.get("params") or {}, ensure_ascii=False),
                universe=result.get("universe"),
                expression=result.get("expression"),
                benchmark=result.get("benchmark"),
                start_date=result.get("start_date") or "",
                end_date=result.get("end_date") or "",
                annual_return=result.get("annual_return"),
                annual_volatility=result.get("annual_volatility"),
                sharpe=result.get("sharpe"),
                sortino=result.get("sortino"),
                max_drawdown=result.get("max_drawdown"),
                calmar=result.get("calmar"),
                win_rate=result.get("win_rate"),
                benchmark_return=result.get("benchmark_return"),
                excess_return=result.get("excess_return"),
                n_trades=result.get("n_trades"),
                metrics=json.dumps(result.get("metrics") or {}, ensure_ascii=False),
                nav_curve=json.dumps(result.get("nav_curve") or {}, ensure_ascii=False),
                trades=json.dumps(result.get("trades") or [], ensure_ascii=False),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row.id
    except Exception:  # noqa: BLE001
        logger.warning("保存经典回测历史失败", exc_info=True)
        return None


async def list_classic_history(limit: int = 20, offset: int = 0) -> tuple[list[dict], int]:
    async with async_session() as session:
        base = select(ClassicBacktestHistory).where(ClassicBacktestHistory.is_deleted == 0)
        total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        rows = (
            await session.execute(
                base.order_by(ClassicBacktestHistory.created_at.desc()).offset(offset).limit(limit)
            )
        ).scalars().all()
        return [_classic_summary(r) for r in rows], total


async def get_classic_history(history_id: int) -> dict | None:
    async with async_session() as session:
        r = await session.get(ClassicBacktestHistory, history_id)
        if r is None or r.is_deleted:
            return None
        return _classic_detail(r)


async def delete_classic_history(history_id: int) -> bool:
    async with async_session() as session:
        r = await session.get(ClassicBacktestHistory, history_id)
        if r is None:
            return False
        r.is_deleted = 1
        await session.commit()
        return True


def _classic_base(r: ClassicBacktestHistory) -> dict:
    return {
        "history_id": r.id,
        "source": "classic",
        "template": r.strategy_key,
        "template_name": r.strategy_name,
        "category": r.category,
        "kind": "factor" if r.is_factor else "rule",
        "symbols": [r.universe] if r.universe else [],
        "benchmark": r.benchmark,
        "start_date": r.start_date,
        "end_date": r.end_date,
        "annual_return": r.annual_return,
        "annual_volatility": r.annual_volatility,
        "sharpe": r.sharpe,
        "sortino": r.sortino,
        "max_drawdown": r.max_drawdown,
        "calmar": r.calmar,
        "win_rate": r.win_rate,
        "benchmark_return": r.benchmark_return,
        "excess_return": r.excess_return,
        "n_trades": r.n_trades,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _classic_summary(r: ClassicBacktestHistory) -> dict:
    d = _classic_base(r)
    d["has_detail"] = True
    return d


def _classic_detail(r: ClassicBacktestHistory) -> dict:
    d = _classic_base(r)
    _metrics = json.loads(r.metrics) if r.metrics else {}
    d["params"] = json.loads(r.params) if r.params else {}
    d["expression"] = r.expression
    d["metrics"] = _metrics or None
    d["nav_curve"] = json.loads(r.nav_curve) if r.nav_curve else None
    d["trades"] = json.loads(r.trades) if r.trades else []
    d["indicator"] = _metrics.get("indicator") if isinstance(_metrics, dict) else None
    for k in ("metrics", "nav_curve", "trades"):
        d.setdefault(k, None)
    return d


# ==================== 合并列表（经典 + 规则） ====================

async def list_combined(limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    """策略库统一历史列表：经典 + 规则两类回测历史，按 created_at 倒序。

    返回含 source 字段（classic/rule），前端据此区分详情/删除/对比来源。
    """
    async with async_session() as session:
        cls_base = select(ClassicBacktestHistory).where(ClassicBacktestHistory.is_deleted == 0)
        rule_base = select(RuleBacktestHistory).where(RuleBacktestHistory.is_deleted == 0)
        total = (
            (await session.execute(select(func.count()).select_from(cls_base.subquery()))).scalar_one()
            + (await session.execute(select(func.count()).select_from(rule_base.subquery()))).scalar_one()
        )
        cls_rows = (await session.execute(cls_base)).scalars().all()
        rule_rows = (await session.execute(rule_base)).scalars().all()

    merged = [_classic_summary(r) for r in cls_rows] + [_rule_base(r) for r in rule_rows]
    for item in merged:
        item.setdefault("source", "rule")
    merged.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return merged[offset:offset + limit], total


# ==================== 对比（读取两类历史 nav/metrics） ====================

async def get_history_nav(history_id: int, source: str) -> dict | None:
    """按 source 读取单条历史的完整详情（对比用，读取 nav_curve/metrics）。"""
    if source == "classic":
        return await get_classic_history(history_id)
    if source == "rule":
        async with async_session() as session:
            r = await session.get(RuleBacktestHistory, history_id)
        if r is None or r.is_deleted:
            return None
        return _rule_detail(r)
    return None