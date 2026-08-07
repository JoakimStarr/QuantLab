"""mining_worker 子进程分派逻辑与失败兜底测试（不经过 TestClient）。"""
import asyncio
import argparse
from unittest.mock import patch, AsyncMock


def test_worker_dispatches_llm_iterative():
    """_run_inner 对 llm 类型调用 mine_with_llm_iterative 且参数透传。"""
    from app.services.mining.mining_worker import _run_inner
    args = argparse.Namespace(
        task_id=42, type="llm",
        params='{"n_rounds":3,"n_candidates":5,"universe":"csi300"}',
    )
    with patch("app.services.mining.mining_worker._task_timeout", return_value=300), \
         patch("app.services.mining.llm_factor.mine_with_llm_iterative", new_callable=AsyncMock) as mock:
        asyncio.run(_run_inner(args, {"n_rounds": 3, "n_candidates": 5, "universe": "csi300"}))
        mock.assert_awaited_once_with(42, n_rounds=3, n_candidates=5, universe="csi300")


def test_worker_dispatches_symbolic():
    from app.services.mining.mining_worker import _run_inner
    args = argparse.Namespace(task_id=7, type="symbolic", params='{"universe":"all"}')
    with patch("app.services.mining.mining_worker._task_timeout", return_value=1800), \
         patch("app.services.mining.symbolic.mine_with_symbolic", new_callable=AsyncMock) as mock:
        asyncio.run(_run_inner(args, {"universe": "all"}))
        mock.assert_awaited_once_with(7, universe="all")


def test_worker_dispatches_automl():
    from app.services.mining.mining_worker import _run_inner
    args = argparse.Namespace(
        task_id=9, type="automl",
        params='{"factor_ids":[1,2],"method":"lightgbm","walk_forward":false}',
    )
    with patch("app.services.mining.mining_worker._task_timeout", return_value=600), \
         patch("app.services.mining.automl.mine_with_automl", new_callable=AsyncMock) as mock:
        asyncio.run(_run_inner(args, {"factor_ids": [1, 2], "method": "lightgbm", "walk_forward": False}))
        mock.assert_awaited_once_with(9, [1, 2], "lightgbm", walk_forward=False, universe=None)


def test_worker_dispatches_text():
    from app.services.mining.mining_worker import _run_inner
    args = argparse.Namespace(task_id=3, type="text", params='{"codes":["sh600000"]}')
    with patch("app.services.mining.mining_worker._task_timeout", return_value=900), \
         patch("app.services.mining.text_factor.mine_with_text", new_callable=AsyncMock) as mock:
        asyncio.run(_run_inner(args, {"codes": ["sh600000"]}))
        mock.assert_awaited_once_with(3, ["sh600000"])


def test_worker_unknown_type_raises():
    """未知类型触发兜底失败路径（_mark_failed + sys.exit → SystemExit）。"""
    import sys
    from app.services.mining.mining_worker import _run_inner
    args = argparse.Namespace(task_id=1, type="bogus", params="{}")
    with patch("app.services.mining.mining_worker._task_timeout", return_value=300), \
         patch("app.services.mining.mining_worker._mark_failed", new_callable=AsyncMock) as mock_fail, \
         patch.object(sys, "exit", side_effect=SystemExit(1)):
        try:
            asyncio.run(_run_inner(args, {}))
            assert False, "应触发失败退出"
        except SystemExit:
            pass
        mock_fail.assert_awaited_once()
