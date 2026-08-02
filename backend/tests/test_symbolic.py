"""符号回归表达式翻译测试。

_translate_program(prog, feature_names) -> str
程序树结构（gplearn Program.program）：
    ("add", "X0", "X1")                        # 二元函数
    ("add", ("mul", "X0", 0.5), "X1")          # 嵌套
    "X0" / 0.5                                  # 终端
"""

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

    def test_max_min_if_not_in_func_map(self):
        """max/min/if/log 语义不同，必须在 _translate_tree 中单独展开（不在简单映射表里）。"""
        assert "max" not in _FUNC_MAP
        assert "min" not in _FUNC_MAP
        assert "if" not in _FUNC_MAP
        assert "log" not in _FUNC_MAP


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
    """表达式翻译测试（树形结构）。"""

    def test_add_translation(self):
        """add 翻译为 Add。"""
        names = ["mom_5", "mom_20"]
        result = _translate_program(("add", "X0", "X1"), names)
        assert result.startswith("Add(")
        assert "add(" not in result.lower() or result.startswith("Add(")

    def test_sub_translation(self):
        """sub 翻译为 Sub。"""
        names = ["mom_5", "mom_20"]
        result = _translate_program(("sub", "X0", "X1"), names)
        assert result.startswith("Sub(")

    def test_mul_translation(self):
        """mul 翻译为 Mul。"""
        names = ["mom_5", "mom_20"]
        result = _translate_program(("mul", "X0", "X1"), names)
        assert result.startswith("Mul(")

    def test_div_translation(self):
        """div 翻译为 Div。"""
        names = ["mom_5", "mom_20"]
        result = _translate_program(("div", "X0", "X1"), names)
        assert result.startswith("Div(")

    def test_feature_substitution(self):
        """Xi 被替换为对应基础特征子表达式。"""
        names = ["mom_5", "mom_20"]
        result = _translate_program(("add", "X0", "X1"), names)
        assert "X0" not in result
        assert "X1" not in result
        assert "$close / Ref($close, 5) - 1" in result
        assert "$close / Ref($close, 20) - 1" in result

    def test_complex_expression(self):
        """复合嵌套表达式翻译。"""
        names = ["mom_5", "mom_20", "vol_20", "vol_60"]
        result = _translate_program(("div", ("add", "X0", "X1"), ("sub", "X2", "X3")), names)
        assert result.startswith("Div(Add(")
        for i in range(4):
            assert f"X{i}" not in result

    def test_constant_terminal(self):
        """常数终端翻译为数字字面量。"""
        names = ["mom_5"]
        result = _translate_program(("mul", "X0", 0.5), names)
        assert result == f"Mul(({_BASE_FEATURES['mom_5']}), 0.5)"

    def test_constant_fold_unary(self):
        """常数作为一元算子参数时数值折叠（Abs(0.5) 会让 qlib 求值崩溃，必须折叠）。"""
        assert _translate_program(("abs", 0.5), []) == "0.5"
        assert _translate_program(("sign", 0.3), []) == "1.0"
        assert _translate_program(("log", 0.0), []) == "-13.815510557964274"

    def test_constant_fold_nested(self):
        """嵌套常数子树整体折叠。"""
        result = _translate_program(("add", ("abs", 0.5), ("mul", 0.2, 0.3)), [])
        assert result == "0.56"

    def test_constant_fold_protected_div(self):
        """除零折叠遵循 gplearn protected division 语义（x1/x2 if x2!=0 else 1.0）。"""
        assert _translate_program(("div", 1.0, 0.0), []) == "1.0"
        assert _translate_program(("div", 1.0, 2.0), []) == "0.5"

    def test_constant_fold_if(self):
        """if 常数折叠：cond>0 取 a，否则取 b。"""
        assert _translate_program(("if", 0.5, 1.0, 2.0), []) == "1.0"
        assert _translate_program(("if", -0.5, 1.0, 2.0), []) == "2.0"

    def test_constant_fold_skipped_when_mixed(self):
        """含特征参数的节点不折叠，仅折叠常数子树。"""
        names = ["mom_5"]
        result = _translate_program(("add", ("abs", 0.5), "X0"), names)
        assert result == f"Add(0.5, ({_BASE_FEATURES['mom_5']}))"

    def test_all_base_features_substituted(self):
        """所有基础特征均可正确替换。"""
        names = list(_BASE_FEATURES.keys())
        last = len(names) - 1
        result = _translate_program(("add", "X0", f"X{last}"), names)
        for i, name in enumerate(names):
            assert f"X{i}" not in result, f"X{i} 未被替换"
        assert _BASE_FEATURES[names[0]] in result
        assert _BASE_FEATURES[names[-1]] in result

    def test_high_index_not_substring_replaced(self):
        """X10/X11 不会被 X1 的替换误伤（树形解析天然正确）。"""
        names = list(_BASE_FEATURES.keys())
        result = _translate_program(("add", "X1", "X10"), names)
        assert "X1" not in result
        assert "X10" not in result
        assert _BASE_FEATURES[names[1]] in result   # 第 2 个特征
        assert _BASE_FEATURES[names[10]] in result  # 第 11 个特征

    def test_single_feature(self):
        """单特征翻译。"""
        names = ["mom_5"]
        result = _translate_program(("add", "X0", "X0"), names)
        assert result == f"Add(({_BASE_FEATURES['mom_5']}), ({_BASE_FEATURES['mom_5']}))"

    def test_max_translated_as_if_greater(self):
        """max 语义 = np.maximum(a,b)，必须展开为 If(Greater(a,b), a, b) 而非 Greater(a,b)。"""
        names = ["mom_5", "mom_20"]
        result = _translate_program(("max", "X0", "X1"), names)
        assert result.startswith("If(Greater(")
        assert result.count("If") == 1

    def test_min_translated_as_if_less(self):
        """min 语义 = np.minimum(a,b)，必须展开为 If(Less(a,b), a, b) 而非 Less(a,b)。"""
        names = ["mom_5", "mom_20"]
        result = _translate_program(("min", "X0", "X1"), names)
        assert result.startswith("If(Less(")
        assert result.count("If") == 1

    def test_if_translated_with_gt_zero(self):
        """if 语义 = np.where(c>0,a,b)，必须展开为 If(Greater(c,0), a, b)。"""
        names = ["mom_5", "mom_20", "vol_20"]
        result = _translate_program(("if", "X0", "X1", "X2"), names)
        assert result.startswith("If(Greater(")
        # 显式 Greater(cond,0)：qlib If 是 cond!=0，与 gplearn cond>0 语义不同，必须补齐
        assert ", 0)" in result

    def test_log_translated_protected(self):
        """log 语义 = log(abs(x)+eps)，必须展开为 Log(Abs(x)+1e-6) 而非 Log(x)。"""
        names = ["mom_5"]
        result = _translate_program(("log", "X0"), names)
        assert result.startswith("Log(Abs(")
        assert "1e-6" in result

    def test_translated_expression_passes_sandbox(self):
        """翻译后的表达式通过沙箱校验。"""
        names = ["mom_5", "mom_20", "vol_20", "vol_60"]
        expr = _translate_program(("div", ("add", "X0", "X1"), ("sub", "X2", "X3")), names)
        assert is_safe_expression(expr), f"翻译结果未通过沙箱: {expr}"

    def test_translated_complex_passes_sandbox(self):
        """复杂翻译表达式（含 max/min/if/log）通过沙箱校验。"""
        names = list(_BASE_FEATURES.keys())
        expr = _translate_program(
            ("div",
             ("add", "X0", ("max", "X1", "X2")),
             ("mul", ("if", "X3", "X4", "X5"), ("log", "X6"))),
            names,
        )
        assert is_safe_expression(expr), f"翻译结果未通过沙箱: {expr}"

    def test_no_function_name_left(self):
        """翻译后不含 gplearn 函数名。"""
        names = ["mom_5", "mom_20"]
        result = _translate_program(("div", ("sub", "X0", "X1"), ("add", "X0", "X1")), names)
        for gname in ("add", "sub", "mul", "div"):
            assert gname not in result, f"残留 gplearn 函数名: {gname}"
