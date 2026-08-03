"""Alpha158 因子导入/补算逻辑测试（DB 层 mock，验证目标因子筛选）。"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.services.factor import alpha158 as alpha158_mod
from app.services.factor.alpha158 import backfill_alpha158_metrics, seed_alpha158


class FakeSession:
    """可记录 execute 收到的查询、返回预设行的假 async session。

    - rows: execute().all() 返回的行
    - existing: execute().scalars().first() 返回的值（模拟"是否已导入"查询）
    """

    def __init__(self, rows, existing=None):
        self._rows = rows
        self._existing = existing
        self.executed_queries = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        pass

    def add(self, *args, **kwargs):
        pass

    async def execute(self, query):
        self.executed_queries.append(query)
        result = MagicMock()
        result.all.return_value = self._rows
        result.scalars.return_value.first.return_value = self._existing
        return result


def patch_db(monkeypatch, rows, existing=None, batch_result=None):
    """打桩 async_session（函数内 from app.core.database import async_session）与 batch_evaluate_alpha158。"""
    from app.core import database as database_mod
    session = FakeSession(rows, existing=existing)
    monkeypatch.setattr(database_mod, "async_session", lambda: session)
    batch = AsyncMock(
        return_value=batch_result or {"ok": True, "evaluated": 0, "failed": 0, "total": 0}
    )
    monkeypatch.setattr(alpha158_mod, "batch_evaluate_alpha158", batch)
    return session, batch


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestBackfillAlpha158Metrics:
    def test_backfill_without_ids_only_targets_null_ic(self, monkeypatch):
        """不传 factor_ids：只查 ic IS NULL 的因子。"""
        session, batch = patch_db(
            monkeypatch, [(5, "Expr5")], batch_result={"ok": True, "evaluated": 1, "failed": 0, "total": 1}
        )
        result = run(backfill_alpha158_metrics())

        assert "IS NULL" in str(session.executed_queries[0])
        batch.assert_awaited_once()
        assert batch.await_args.kwargs["factor_ids"] == [5]
        assert result["evaluated"] == 1

    def test_backfill_with_ids_targets_only_selected(self, monkeypatch):
        """传 factor_ids：只重算所选因子，且不要求 ic 为 NULL。"""
        session, batch = patch_db(
            monkeypatch, [(1, "Expr1"), (2, "Expr2")],
            batch_result={"ok": True, "evaluated": 2, "failed": 0, "total": 2},
        )
        result = run(backfill_alpha158_metrics(factor_ids=[1, 2]))

        assert "IN" in str(session.executed_queries[0])
        assert "IS NULL" not in str(session.executed_queries[0])
        batch.assert_awaited_once()
        assert batch.await_args.kwargs["factor_ids"] == [1, 2]
        assert result["evaluated"] == 2

    def test_backfill_with_ids_no_match(self, monkeypatch):
        """所选因子不存在：不触发评价，返回提示。"""
        _, batch = patch_db(monkeypatch, [])
        result = run(backfill_alpha158_metrics(factor_ids=[999]))

        batch.assert_not_awaited()
        assert "所选因子不存在" in result["message"]

    def test_backfill_all_evaluated(self, monkeypatch):
        """无缺指标因子且未指定 ids：提示无需补算。"""
        _, batch = patch_db(monkeypatch, [])
        result = run(backfill_alpha158_metrics())

        batch.assert_not_awaited()
        assert "无需补算" in result["message"]


class TestSeedAlpha158:
    def test_seed_fresh_import_evaluates_all(self, monkeypatch):
        """全新库：导入 158 个因子并全量评价。"""
        session, batch = patch_db(
            monkeypatch, [], existing=None,
            batch_result={"ok": True, "evaluated": 158, "failed": 0, "total": 158},
        )
        result = run(seed_alpha158())

        assert session._existing is None
        assert batch.await_args.kwargs.get("factor_ids") is None
        assert result["count"] == 158
        assert result["already_imported"] is False

    def test_seed_already_imported_with_missing_backfills(self, monkeypatch):
        """已导入但存在缺指标因子：只补算缺指标的。"""
        session, batch = patch_db(
            monkeypatch, [(3,), (4,)], existing=object(),
            batch_result={"ok": True, "evaluated": 2, "failed": 0, "total": 2},
        )
        result = run(seed_alpha158())

        assert result["already_imported"] is True
        assert batch.await_args.kwargs["factor_ids"] == [3, 4]
        assert result["evaluated"] == 2
        assert "补算" in result["message"]

    def test_seed_already_imported_all_evaluated(self, monkeypatch):
        """已导入且全部有指标：不触发评价。"""
        _, batch = patch_db(monkeypatch, [], existing=object())
        result = run(seed_alpha158())

        batch.assert_not_awaited()
        assert result["already_imported"] is True
        assert "无需重复操作" in result["message"]
