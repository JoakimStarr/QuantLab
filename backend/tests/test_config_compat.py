"""Config 层 Pydantic 兼容 + placeholder 检测测试。"""

import pytest

from app.core.config import (
    AppSettings,
    MiningSettings,
    QuantSettings,
    SecuritySettings,
    TaskSettings,
    is_placeholder_api_key,
    settings,
)


class TestCompatLayer:
    """验证 Pydantic 模型既支持属性访问，也兼容 dict.get 调用。"""

    def test_settings_quant_attribute_access(self):
        assert settings.quant.universe == "csi300"

    def test_settings_quant_dict_get(self):
        # 旧代码大量使用 settings.quant.get("xxx", default)
        assert settings.quant.get("universe") == "csi300"
        assert settings.quant.get("nonexistent", "fallback") == "fallback"

    def test_settings_task_get(self):
        assert settings.task.get("max_concurrent", 2) == settings.task.max_concurrent

    def test_settings_getitem(self):
        # 也兼容 obj["key"] 形式
        assert settings.quant["universe"] == "csi300"
        with pytest.raises(KeyError):
            _ = settings.quant["nonexistent"]

    def test_backward_compat_properties(self):
        # settings.security.auth_enabled / secret_key / app_env 等安全属性
        assert isinstance(settings.security.auth_enabled, bool)
        assert isinstance(settings.security.secret_key, str)
        assert isinstance(settings.security.app_env, str)


class TestPlaceholderDetection:
    """AI API Key 占位符检测。"""

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "your_glm_api_key_here",
            "your_siliconflow_api_key_here",
            "your_opencodezen_api_key_here",
            "your_api_key_here",
        ],
    )
    def test_placeholder_values(self, value):
        assert is_placeholder_api_key(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "sk-abc123",
            "glm-real-key",
        ],
    )
    def test_real_keys(self, value):
        assert is_placeholder_api_key(value) is False

    def test_none_value(self):
        assert is_placeholder_api_key(None) is True


class TestPydanticDefaults:
    """Pydantic 模型默认值兜底（缺失字段不报错）。"""

    def test_app_defaults(self):
        a = AppSettings()
        assert a.name == "QuantLab"
        assert a.timezone == "Asia/Shanghai"

    def test_quant_defaults(self):
        q = QuantSettings()
        assert q.topk == 50
        assert q.cost_buy == 0.0013
        assert q.default_backtest_period["start"] == "2020-01-01"

    def test_mining_allowed_ops_default(self):
        m = MiningSettings()
        assert "Ref" in m.llm["allowed_ops"]
        assert m.llm["candidates_per_run"] == 10

    def test_task_timeouts_default(self):
        t = TaskSettings()
        assert t.timeouts["llm_hard_limit_seconds"] == 7200

    def test_security_defaults(self):
        s = SecuritySettings()
        assert s.app_env == "development"
        assert s.auth_enabled is False
