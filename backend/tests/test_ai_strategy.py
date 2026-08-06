# -*- coding: utf-8 -*-
"""ai_strategy 单元测试。

覆盖 AI 生成策略偏好扩展：
- 资金 capital 透传 create_strategy 并落库
- 资金/风格/风险/其他要求写入 description 标签（可追溯）
- other 超长截断（前后端一致的 300 字上限）
"""
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

from app.services.strategy import ai_strategy as svc


@pytest.fixture
def qualified_factors():
    return [
        {"id": 1, "name": "f_mom", "ic": 0.03, "icir": 0.5,
         "category": "动量", "expression": "$close/Ref($close,5)-1"},
        {"id": 2, "name": "f_val", "ic": -0.03, "icir": -0.6,
         "category": "价值", "expression": "$pb_mrq"},
    ]


def _patch_deps(qualified_factors, raw):
    """启动全部 mock，返回 (create_mock, ExitStack) 供测试结束关闭。"""
    create_mock = AsyncMock(return_value={"id": 99, "name": "AI策略-x"})
    stack = ExitStack()
    stack.enter_context(
        patch.object(svc, "_load_qualified_factors", new=AsyncMock(return_value=qualified_factors))
    )
    stack.enter_context(patch.object(svc, "_load_correlation_hint", new=AsyncMock(return_value=None)))
    stack.enter_context(patch.object(svc, "_existing_strategy_suffix", new=AsyncMock(return_value=0)))
    # create_strategy 在函数体内延迟导入，patch 其源头模块
    stack.enter_context(patch("app.services.strategy.manager.create_strategy", new=create_mock))
    stack.enter_context(patch.object(svc, "call_llm_json", new=AsyncMock(return_value=raw)))
    return create_mock, stack


@pytest.mark.asyncio
async def test_capital_and_other_passed_to_create(qualified_factors):
    """资金/其他要求应透传 create_strategy 并写入 description 标签。"""
    raw = {"factor_ids": [1, 2], "topk": 50, "n_drop": 5, "rebalance_freq": "day",
           "combination_method": "equal_weight", "rationale": "test rationale"}
    create_mock, stack = _patch_deps(qualified_factors, raw)
    try:
        result = await svc.generate_strategy_with_ai(
            capital=10000000, other=" 希望低换手，规避科创板 ",
            style="momentum", risk_tolerance="conservative", rebalance_pref="month",
        )
    finally:
        stack.close()
    assert result["strategy"]["id"] == 99

    kw = create_mock.await_args.kwargs
    # 全部偏好存进单个 ai_prefs 字段
    assert kw["ai_prefs"] == {
        "style": "momentum",
        "risk_tolerance": "conservative",
        "rebalance_pref": "month",
        "capital": 10000000,
        "other": "希望低换手，规避科创板",
    }
    desc = kw["description"]
    assert "[AI生成]" in desc
    assert "[资金1,000万]" in desc
    assert "[动量]" in desc
    assert "[稳健]" in desc
    assert "[月调仓]" in desc
    assert "[要求:希望低换手，规避科创板]" in desc
    assert "test rationale" in desc
    # 调仓频率由 LLM 输出决定（用户偏好仅作为约束提示，不覆盖 LLM 决策）
    assert kw["rebalance_freq"] == "day"


@pytest.mark.asyncio
async def test_other_truncated_to_maxlen(qualified_factors):
    """other 超长时应截断到 _OTHER_MAXLEN，避免 prompt 被异常撑大。"""
    raw = {"factor_ids": [1, 2], "topk": 50, "n_drop": 5, "rebalance_freq": "day",
           "combination_method": "equal_weight", "rationale": "x"}
    long_other = "需求" * 500  # 1000 字符，远超上限
    create_mock, stack = _patch_deps(qualified_factors, raw)
    try:
        await svc.generate_strategy_with_ai(other=long_other)
    finally:
        stack.close()
    kw = create_mock.await_args.kwargs
    ai_prefs = kw["ai_prefs"]
    assert len(ai_prefs["other"]) == 300
    assert "需求" * 150 not in kw["description"]  # description 标签只取前 60 字
    assert len(kw["description"]) <= 400


@pytest.mark.asyncio
async def test_ai_prefs_none_when_no_preferences(qualified_factors):
    """未填任何偏好时 ai_prefs 应为 None（不存空 JSON）。"""
    raw = {"factor_ids": [1, 2], "topk": 50, "n_drop": 5, "rebalance_freq": "day",
           "combination_method": "equal_weight", "rationale": "x"}
    create_mock, stack = _patch_deps(qualified_factors, raw)
    try:
        await svc.generate_strategy_with_ai()
    finally:
        stack.close()
    kw = create_mock.await_args.kwargs
    assert kw["ai_prefs"] is None
    assert "资金" not in kw["description"]
