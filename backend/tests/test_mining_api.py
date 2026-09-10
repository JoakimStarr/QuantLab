"""mining API 序列化 + ai_explain 模型因子守卫测试（纯单元，不依赖 DB）。"""
import json
from unittest.mock import AsyncMock, patch

import pytest


def _make_task(**kw):
    from app.models.mining_task import MiningTask

    t = MiningTask(id=1, type="automl", status="done")
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def test_task_dict_surfaces_shap_importance():
    """详情路径应暴露 AutoML 存在 params.result 里的 SHAP 重要性。"""
    from app.api.mining import _task_dict

    t = _make_task(params=json.dumps({
        "method": "linear",
        "result": {"shap_importance": [["mom20", 0.5], ["rev5", 0.2]]},
    }))
    d = _task_dict(t)
    assert d["shap_importance"] == [["mom20", 0.5], ["rev5", 0.2]]


def test_task_dict_list_path_defers_result():
    """列表路径（include_result=False）不反序列化 result / SHAP。"""
    from app.api.mining import _task_dict

    t = _make_task(
        params=json.dumps({"result": {"shap_importance": [["mom20", 0.5]]}}),
        result=json.dumps({"improvement_curve": [0.01, 0.02], "stopped_early": True}),
    )
    d = _task_dict(t, include_result=False)
    assert d["shap_importance"] is None
    assert d["improvement_curve"] is None
    assert d["stopped_early"] is None
    # 详情路径完整返回
    d2 = _task_dict(t)
    assert d2["shap_importance"] == [["mom20", 0.5]]
    assert d2["improvement_curve"] == [0.01, 0.02]
    assert d2["stopped_early"] is True


def test_is_model_factor():
    from app.services.factor.ai_explain import _is_model_factor

    assert _is_model_factor("AutoML(linear,7)")
    assert _is_model_factor("AutoML(walk_forward,3)")
    assert _is_model_factor("TextSentiment(news_score_5)")
    assert not _is_model_factor("Mean($close, 5)")
    assert not _is_model_factor("Ref($close, -20) / $close - 1")
    assert not _is_model_factor(None)


@pytest.mark.asyncio
async def test_explain_factor_skips_llm_for_model_factor():
    """模型型因子不调用 LLM；普通表达式仍调用。"""
    from app.services.factor import ai_explain

    calls = []

    async def fake_call(expr, name=None):
        calls.append(expr)
        return {"summary": "ok"}

    with patch.object(ai_explain, "_call_llm", new=AsyncMock(side_effect=fake_call)) as mock:
        out = await ai_explain.explain_factor("AutoML(linear,7)", "automl_7")
        assert "模型型因子" in out["summary"]
        assert mock.await_count == 0
        assert calls == []

        await ai_explain.explain_factor("Mean($close, 5)", "ma5")
        assert mock.await_count == 1
        assert calls == ["Mean($close, 5)"]


class _FakeResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeSession:
    def __init__(self, factory):
        self._factory = factory

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return _FakeResult(self._factory.factor)

    async def commit(self):
        self._factory.commits += 1


@pytest.mark.asyncio
async def test_explain_and_update_model_factor_skips_llm():
    """模型型因子落库时写固定说明，不调用 LLM。"""
    from app.models.factor import Factor
    from app.services.factor import ai_explain

    factor = Factor(id=42, name="automl_7", expression="AutoML(linear,7)", description=None)

    class Factory:
        def __init__(self):
            self.factor = factor
            self.commits = 0

        def __call__(self):
            return _FakeSession(self)

    factory = Factory()
    with patch("app.core.database.async_session", factory), \
            patch.object(ai_explain, "_call_llm", new=AsyncMock()) as mock:
        out = await ai_explain.explain_and_update_factor(42)

    assert mock.await_count == 0
    assert out["cached"] is False
    assert "模型型因子" in out["description"]
    assert factor.ai_explanation is not None
    assert json.loads(factor.ai_explanation)["summary"] == out["description"]
    assert factory.commits >= 1
