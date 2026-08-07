"""策略库规则回测历史存取。

每次 /strategy-library/backtest 自动保存一条（配置 + 结果快照），供策略库页面
下方列表回看/重跑/删除。列表接口返回轻量摘要（不含 metrics/nav_curve/trades 大字段），
详情接口返回全量（含 params/metrics/nav_curve/trades）。

保存失败不阻断回测：save_history 内部吞掉异常只记 warning，返回 None。
"""
import json
import logging

from sqlalchemy import func, select

from app.core.database import async_session
from app.models.rule_backtest_history import RuleBacktestHistory

logger = logging.getLogger(__name__)

# 列表接口省略的大字段（单条可达 300KB+，避免列表载荷膨胀）
_BIG_FIELDS = ("metrics", "nav_curve", "trades")


async def save_history(result: dict) -> int | None:
    """把 run_rule_backtest 的返回结果落库，返回新 id；异常只记 warning，不阻断回测。"""
    try:
        async with async_session() as session:
            row = RuleBacktestHistory(
                template=result.get("template") or "",
                template_name=result.get("name") or "",
                category=result.get("category"),
                kind=result.get("kind"),
                params=json.dumps(result.get("params") or {}, ensure_ascii=False),
                symbols=json.dumps(result.get("symbols") or [], ensure_ascii=False),
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
        logger.warning("保存策略库回测历史失败", exc_info=True)
        return None


async def list_history(limit: int = 20, offset: int = 0) -> tuple[list[dict], int]:
    """按时间倒序返回轻量摘要列表（不含 metrics/nav_curve/trades）。"""
    async with async_session() as session:
        base = select(RuleBacktestHistory).where(RuleBacktestHistory.is_deleted == 0)
        total = (
            await session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        rows = (
            await session.execute(
                base.order_by(RuleBacktestHistory.created_at.desc()).offset(offset).limit(limit)
            )
        ).scalars().all()
        return [_summary(r) for r in rows], total


async def get_history(history_id: int) -> dict | None:
    async with async_session() as session:
        r = await session.get(RuleBacktestHistory, history_id)
        if r is None or r.is_deleted:
            return None
        return _detail(r)


async def delete_history(history_id: int) -> bool:
    """软删除历史记录。"""
    async with async_session() as session:
        r = await session.get(RuleBacktestHistory, history_id)
        if r is None:
            return False
        r.is_deleted = 1
        await session.commit()
        return True


def _initial_capital(r: RuleBacktestHistory) -> float | None:
    """初始资金从 metrics JSON 快照提取（保存时已注入 metrics）。"""
    if not r.metrics:
        return None
    try:
        return (json.loads(r.metrics) or {}).get("initial_capital")
    except (ValueError, TypeError):
        return None


def _base(r: RuleBacktestHistory) -> dict:
    return {
        "history_id": r.id,
        "template": r.template,
        "template_name": r.template_name,
        "category": r.category,
        "kind": r.kind,
        "symbols": json.loads(r.symbols) if r.symbols else [],
        "benchmark": r.benchmark,
        "initial_capital": _initial_capital(r),
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


def _summary(r: RuleBacktestHistory) -> dict:
    """列表轻量摘要：省略 metrics/nav_curve/trades 大字段。"""
    d = _base(r)
    d["has_detail"] = True
    return d


def _detail(r: RuleBacktestHistory) -> dict:
    """完整详情：含 params/metrics/nav_curve/trades。

    注意不输出 `id` 字段（顶层用 history_id），避免前端把 history_id 当 backtest_result
    id 传给 BacktestResultDetail 触发蒙特卡罗请求（规则策略无对应结果，会 404）。
    """
    d = _base(r)
    _metrics = json.loads(r.metrics) if r.metrics else {}
    d["params"] = json.loads(r.params) if r.params else {}
    d["metrics"] = _metrics or None
    d["nav_curve"] = json.loads(r.nav_curve) if r.nav_curve else None
    d["trades"] = json.loads(r.trades) if r.trades else []
    # 指标线（K 线图叠加）随 metrics 快照保存，详情时还原到顶层供前端直接使用
    d["indicator"] = _metrics.get("indicator") if isinstance(_metrics, dict) else None
    for k in _BIG_FIELDS:
        d.setdefault(k, None)
    return d
