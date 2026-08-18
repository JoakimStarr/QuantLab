"""LLM 迭代挖掘早停机制测试。

场景：best IC 持续无改善时，任务应在 stall_tolerance（默认 2）轮后提前终止，
而非跑满 n_rounds 全部轮次（每轮一次 LLM 调用，是最贵的环节）。
"""
import asyncio
from unittest.mock import AsyncMock, patch


def _fake_eval_result(valid_ic: float = 0.05) -> dict:
    """构造一个通过多维验证的候选评价结果。"""
    return {
        "passed": True,
        "valid_ic": valid_ic,
        "rank_ic": valid_ic * 0.8,
        "icir": 0.5,
        "valid_ic_series": [valid_ic] * 10,
        "significance": {"p_value": 0.001},
        "fail_reasons": [],
    }


def _run(n_rounds: int = 5):
    from app.services.mining.llm_factor import iterative_mine_factors

    template = {"prompt": "", "llm_prompt": "p", "base_features": [], "allowed_ops": []}
    candidates = [{"name": "f1", "expression": "$close / Ref($close, 5) - 1", "description": "d"}]

    with patch("app.services.mining.llm_factor._call_llm", new_callable=AsyncMock,
               return_value=[dict(candidates[0])]) as mock_llm, \
         patch("app.services.mining.llm_factor.validate_expression",
               side_effect=lambda e: e), \
         patch("app.services.mining.llm_factor._load_existing_ic_series",
               new_callable=AsyncMock, return_value=[]), \
         patch("app.services.mining.llm_factor._evaluate_bounded",
               new_callable=AsyncMock, side_effect=lambda *a, **k: _fake_eval_result()), \
         patch("app.services.mining.llm_factor.add_factor",
               new_callable=AsyncMock, return_value={"id": 1}), \
         patch("app.services.mining.llm_factor.update_factor_metrics",
               new_callable=AsyncMock), \
         patch("app.services.mining.llm_factor._update_task",
               new_callable=AsyncMock), \
         patch("app.services.mining.candidate_store.upsert_candidates",
               new_callable=AsyncMock):
        result = asyncio.run(
            iterative_mine_factors(template, n_rounds=n_rounds, candidates_per_round=1,
                                   task_id=None, universe=None))
        return result, mock_llm


def test_early_stop_on_stalled_ic():
    """连续 stall_tolerance=2 轮无改善 → 5 轮任务只跑 3 轮即停。"""
    result, mock_llm = _run(n_rounds=5)
    assert result["stopped_early"] is True
    assert "无改善" in result["stop_reason"]
    assert len(result["rounds"]) == 3
    # LLM 仅被调用 3 次（而非 5 次）
    assert mock_llm.await_count == 3


def test_no_early_stop_when_improving():
    """每轮 IC 递增（有改善）→ 不早停，跑满全部轮次。"""
    from app.services.mining.llm_factor import iterative_mine_factors

    template = {"prompt": "", "llm_prompt": "p", "base_features": [], "allowed_ops": []}
    candidates = [{"name": "f1", "expression": "$close / Ref($close, 5) - 1", "description": "d"}]
    ic_values = iter([0.05, 0.10, 0.15])

    with patch("app.services.mining.llm_factor._call_llm", new_callable=AsyncMock,
               return_value=candidates) as mock_llm, \
         patch("app.services.mining.llm_factor.validate_expression",
               side_effect=lambda e: e), \
         patch("app.services.mining.llm_factor._load_existing_ic_series",
               new_callable=AsyncMock, return_value=[]), \
         patch("app.services.mining.llm_factor._evaluate_bounded",
               new_callable=AsyncMock, side_effect=lambda *a, **k: _fake_eval_result(next(ic_values))), \
         patch("app.services.mining.llm_factor.add_factor",
               new_callable=AsyncMock, return_value={"id": 1}), \
         patch("app.services.mining.llm_factor.update_factor_metrics",
               new_callable=AsyncMock), \
         patch("app.services.mining.llm_factor._update_task",
               new_callable=AsyncMock), \
         patch("app.services.mining.candidate_store.upsert_candidates",
               new_callable=AsyncMock):
        result = asyncio.run(
            iterative_mine_factors(template, n_rounds=3, candidates_per_round=1,
                                   task_id=None, universe=None))
        assert result["stopped_early"] is False
        assert result["stop_reason"] is None
        assert len(result["rounds"]) == 3
        assert mock_llm.await_count == 3
