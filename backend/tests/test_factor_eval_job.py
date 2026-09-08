# -*- coding: utf-8 -*-
"""因子评价后台 job（factor_eval_job）服务层测试。"""
import json
import os

import pytest

from app.services.factor.eval_jobs import (
    cancel_eval_job,
    create_eval_job,
    get_eval_job,
    list_eval_jobs,
    recover_stale_eval_jobs,
)


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="需要 DB")
async def test_create_and_get_eval_job():
    job = await create_eval_job("single", [1, 2, 3], "2024-01-01", "2024-12-31", "csi300")
    assert job["status"] == "pending"
    assert job["total"] == 3
    assert job["factor_ids"] == [1, 2, 3]

    detail = await get_eval_job(job["id"])
    assert detail["id"] == job["id"]
    assert "result" in detail  # 详情含 result 字段


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="需要 DB")
async def test_list_omits_big_result_field():
    await create_eval_job("batch", [7])
    items, total = await list_eval_jobs(limit=10)
    assert total >= 1
    assert all("result" not in item for item in items)


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="需要 DB")
async def test_cancel_job():
    job = await create_eval_job("batch", [5])
    ok = await cancel_eval_job(job["id"])
    assert ok is True
    detail = await get_eval_job(job["id"])
    assert detail["status"] == "cancelled"
    # 幂等：再次取消返回 True 且状态不变
    assert await cancel_eval_job(job["id"]) is True


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="需要 DB")
async def test_recover_stale_marks_dead_running_failed():
    from datetime import datetime

    from app.core.database import async_session
    from app.models.factor_eval_job import FactorEvalJob

    # 造一个 running 但无存活 worker（无 pid 文件）的 job
    job = await create_eval_job("batch", [1])
    async with async_session() as session:
        r = await session.get(FactorEvalJob, job["id"])
        r.status = "running"
        r.started_at = datetime.now()
        await session.commit()

    await recover_stale_eval_jobs()

    detail = await get_eval_job(job["id"])
    assert detail["status"] == "failed"
    assert "zombie" in (detail["error"] or "")
