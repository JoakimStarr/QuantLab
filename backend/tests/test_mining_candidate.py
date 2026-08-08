"""mining_candidate 候选落库测试。

验证候选因子（含未通过/被拒的）能落库、同表达式幂等更新、
状态随挖掘阶段推进（generated → rejected/passed）。
"""
import asyncio
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.mining_candidate import MiningCandidate


@pytest.mark.asyncio
async def test_upsert_candidates_insert_and_dedup():
    from app.services.mining.candidate_store import upsert_candidates, list_candidates
    from app.core.database import async_session

    await upsert_candidates(1, [
        {"name": "mom20", "expression": "$close / Ref($close, 20) - 1",
         "description": "20日动量", "status": "generated"},
        {"name": "bad_expr", "expression": "$close + ",
         "description": "", "status": "generated"},
    ])
    await upsert_candidates(1, [
        # 同一表达式再次写入 → 幂等更新不重复插入
        {"name": "mom20", "expression": "$close / Ref($close, 20) - 1",
         "description": "20日动量", "status": "passed", "ic": 0.045, "rank_ic": 0.05},
    ], round_no=2)
    # 另一任务的同表达式互不干扰
    await upsert_candidates(2, [
        {"name": "mom20", "expression": "$close / Ref($close, 20) - 1",
         "description": "20日动量", "status": "generated"},
    ])

    async with async_session() as session:
        rows = (await session.execute(
            select(MiningCandidate).where(MiningCandidate.task_id == 1)
        )).scalars().all()
        assert len(rows) == 2, "同任务同表达式只应有一行"
        by_expr = {r.expression: r for r in rows}
        mom = by_expr["$close / Ref($close, 20) - 1"]
        assert mom.status == "passed"
        assert mom.ic == 0.045
        assert mom.round == 2, "round 应更新为最新轮次"
        bad = by_expr["$close + "]
        assert bad.status == "generated"

    # 另一任务的行数独立
    async with async_session() as session:
        rows = (await session.execute(
            select(MiningCandidate).where(MiningCandidate.task_id == 2)
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "generated"

    # 按任务查询
    items = await list_candidates(1)
    assert len(items) == 2
    assert items[0]["expression"] == "$close + "
    assert items[1]["status"] == "passed"
    assert items[1]["ic"] == 0.045


@pytest.mark.asyncio
async def test_upsert_rejected_with_reason():
    from app.services.mining.candidate_store import upsert_candidates
    from app.core.database import async_session

    await upsert_candidates(3, [
        {"name": "", "expression": "Ref($close, -5)",
         "description": "", "status": "rejected", "reason": "沙箱拒绝: 禁止负Ref"},
    ])
    async with async_session() as session:
        rows = (await session.execute(
            select(MiningCandidate).where(MiningCandidate.task_id == 3)
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "rejected"
        assert "负Ref" in rows[0].reason


@pytest.mark.asyncio
async def test_candidates_api_endpoint(db_ready):
    """GET /mining/tasks/{id}/candidates 返回该任务候选（含状态/原因字段）。

    用 httpx ASGITransport 在 pytest-asyncio 的 loop 内直连 app（不走 TestClient
    的独立 loop，避免 asyncpg 连接跨 loop 冲突）；lifespan 不触发，表已由
    session 级 _create_tables 建好。
    """
    if not db_ready:
        pytest.skip("DB 不可用")
    import httpx

    from app.main import app
    from app.services.mining.candidate_store import upsert_candidates

    await upsert_candidates(11, [
        {"name": "mom", "expression": "$close / Ref($close, 20) - 1",
         "description": "动量", "status": "passed", "ic": 0.045},
        {"name": "bad", "expression": "Ref($close, -5)", "description": "",
         "status": "rejected", "reason": "沙箱拒绝: 禁止负Ref"},
    ])
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/v1/mining/tasks/11/candidates")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["total"] == 2
    statuses = {i["expression"]: i["status"] for i in data["items"]}
    assert statuses["$close / Ref($close, 20) - 1"] == "passed"
    assert statuses["Ref($close, -5)"] == "rejected"
    reason = next(i for i in data["items"] if i["expression"] == "Ref($close, -5)")["reason"]
    assert "负Ref" in reason
    assert data["items"][0]["round"] == 1
