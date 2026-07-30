"""符号回归表达式翻译测试。

_translate_program(prog_str, feature_names) -> str
_FUNC_MAP = {"add": "Add", "sub": "Sub", "mul": "Mul", "div": "Div"}
"""
import pytest
from app.services.mining.symbolic import (
    _translate_program,
    _FUNC_MAP,
    _BASE_FEATURES,
)
from app.services.factor.expression import is_safe_expression


class TestFuncMap:
    """函数映射测试。"""

    def test_func_map_contains_basic_ops(self):
        """_FUNC_MAP 包含 add/sub/mul/div 四则运算映射。"""
        basic_ops = {"add": "Add", "sub": "Sub", "mul": "Mul", "div": "Div"}
        for k, v in basic_ops.items():
            assert k in _FUNC_MAP, f"缺少函数映射: {k}"
            assert _FUNC_MAP[k] == v, f"函数映射错误: {k} -> {_FUNC_MAP.get(k)}"


class TestBaseFeatures:
    """基础特征定义测试。"""

    def test_base_features_non_empty(self):
        """基础特征非空。"""
        assert len(_BASE_FEATURES) > 0

    def test_base_features_use_valid_fields(self):
        """基础特征表达式只使用白名单字段。"""
        for name, expr in _BASE_FEATURES.items():
            assert is_safe_expression(expr), f"基础特征 {name} 表达式不安全: {expr}"

    def test_base_features_no_lookahead(self):
        """基础特征不含负数 Ref（无 look-ahead bias）。"""
        for name, expr in _BASE_FEATURES.items():
            # 负数 Ref 会被沙箱拒绝
            assert is_safe_expression(expr), f"基础特征 {name} 含未来数据: {expr}"


class TestTranslation:
    """表达式翻译测试。"""

    def test_add_translation(self):
        """add 翻译为 Add。"""
        names = ["mom_5", "mom_20"]
        result = _translate_program("add(X0, X1)", names)
        assert result.startswith("Add(")
        # 残留的 gplearn 函数名应为 0（区分大小写：Add 不含 add）
        assert "add(" not in result

    def test_sub_translation(self):
        """sub 翻译为 Sub。"""
        names = ["mom_5", "mom_20"]
        result = _translate_program("sub(X0, X1)", names)
        assert result.startswith("Sub(")

    def test_mul_translation(self):
        """mul 翻译为 Mul。"""
        names = ["mom_5", "mom_20"]
        result = _translate_program("mul(X0, X1)", names)
        assert result.startswith("Mul(")

    def test_div_translation(self):
        """div 翻译为 Div。"""
        names = ["mom_5", "mom_20"]
        result = _translate_program("div(X0, X1)", names)
        assert result.startswith("Div(")

    def test_feature_substitution(self):
        """Xi 被替换为对应基础特征子表达式。"""
        names = ["mom_5", "mom_20"]
        result = _translate_program("add(X0, X1)", names)
        assert "X0" not in result
        assert "X1" not in result
        # mom_5 = "$close / Ref($close, 5) - 1"
        assert "$close / Ref($close, 5) - 1" in result
        # mom_20 = "$close / Ref($close, 20) - 1"
        assert "$close / Ref($close, 20) - 1" in result

    def test_complex_expression(self):
        """复合嵌套表达式翻译。"""
        names = ["mom_5", "mom_20", "vol_20", "vol_60"]
        result = _translate_program("div(add(X0, X1), sub(X2, X3))", names)
        assert result.startswith("Div(Add(")
        assert "X0" not in result
        assert "X1" not in result
        assert "X2" not in result
        assert "X3" not in result

    def test_all_base_features_substituted(self):
        """所有 12 个基础特征均可正确替换。"""
        names = list(_BASE_FEATURES.keys())
        result = _translate_program("add(X0, X11)", names)
        for i, name in enumerate(names):
            assert f"X{i}" not in result, f"X{i} 未被替换"
        # 验证 X0 和 X11 的子表达式都在结果中
        assert _BASE_FEATURES[names[0]] in result
        assert _BASE_FEATURES[names[-1]] in result

    def test_high_index_not_substring_replaced(self):
        """X10/X11 不会被 X1 的替换误伤。"""
        names = list(_BASE_FEATURES.keys())  # 12 个特征
        result = _translate_program("add(X1, X10)", names)
        # X1 应替换为 mom_20 子表达式, X10 应替换为 ma_div_20 子表达式
        assert "X1" not in result
        assert "X10" not in result
        assert _BASE_FEATURES[names[1]] in result   # mom_20
        assert _BASE_FEATURES[names[10]] in result  # ma_div_20

    def test_single_feature(self):
        """单特征翻译。"""
        names = ["mom_5"]
        result = _translate_program("add(X0, X0)", names)
        assert result == f"Add(({_BASE_FEATURES['mom_5']}), ({_BASE_FEATURES['mom_5']}))"

    def test_translated_expression_passes_sandbox(self):
        """翻译后的表达式通过沙箱校验。"""
        names = ["mom_5", "mom_20", "vol_20", "vol_60"]
        expr = _translate_program("div(add(X0, X1), sub(X2, X3))", names)
        assert is_safe_expression(expr), f"翻译结果未通过沙箱: {expr}"

    def test_translated_complex_passes_sandbox(self):
        """复杂翻译表达式通过沙箱校验。"""
        names = list(_BASE_FEATURES.keys())
        expr = _translate_program("div(add(X0, X1), mul(X2, X3))", names)
        assert is_safe_expression(expr), f"翻译结果未通过沙箱: {expr}"

    def test_no_function_name_left(self):
        """翻译后不含 gplearn 函数名。"""
        names = ["mom_5", "mom_20"]
        result = _translate_program("div(sub(X0, X1), add(X0, X1))", names)
        for gname in _FUNC_MAP:
            assert gname not in result, f"残留 gplearn 函数名: {gname}"
