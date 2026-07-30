"""因子表达式沙箱安全测试。

validate_expression(expr) -> str  (抛 ExpressionValidationError 表示不安全)
is_safe_expression(expr) -> bool
"""
import pytest
from app.services.factor.expression import (
    validate_expression,
    is_safe_expression,
    ExpressionValidationError,
)


class TestExpressionSandbox:
    """表达式沙箱安全测试。"""

    @pytest.mark.parametrize("expr", [
        "Mean($close, 5)",
        "Ref($close, 1)",
        "Std($volume, 10)",
        "Rank($close)",
        "Corr($close, $volume, 5)",
        "($close - $low) / $close",
        "Delta($close, 5)",
        "EMA($close, 10)",
        "$close + $high",
        "Greater($close, $low)",
    ])
    def test_valid_whitelist_ops(self, expr):
        """白名单算子与字段应通过校验。"""
        result = validate_expression(expr)
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.parametrize("expr", [
        "import os; os.system('rm -rf /')",
        "__import__('subprocess').call(['ls'])",
        "exec('print(1)')",
        "eval('1+1')",
        "open('/etc/passwd').read()",
        "lambda x: x",
        "globals()",
        "getattr($close, 'x')",
    ])
    def test_dangerous_keywords_blocked(self, expr):
        """危险关键字应被拦截。"""
        with pytest.raises(ExpressionValidationError):
            validate_expression(expr)

    @pytest.mark.parametrize("expr", [
        "Ref($close, -1)",
        "Ref($close, -5)",
        "Mean(Ref($close, -1), 5)",
        "Ref($close,-10)",
    ])
    def test_negative_ref_blocked(self, expr):
        """负数 Ref 应被拦截（防 look-ahead bias）。"""
        with pytest.raises(ExpressionValidationError):
            validate_expression(expr)

    @pytest.mark.parametrize("expr", [
        "$secret_field",
        "$__class__",
        "$_private",
        "$price",
    ])
    def test_non_whitelist_field_blocked(self, expr):
        """非白名单字段应被拦截。"""
        with pytest.raises(ExpressionValidationError):
            validate_expression(expr)

    def test_length_limit(self):
        """超长表达式应被拦截。"""
        long_expr = "Mean($close, 5)" + " + Mean($close, 5)" * 200
        with pytest.raises(ExpressionValidationError):
            validate_expression(long_expr)

    def test_custom_max_length(self):
        """自定义 max_length 生效。"""
        with pytest.raises(ExpressionValidationError):
            validate_expression("Mean($close, 5)", max_length=5)

    def test_empty_expression(self):
        """空表达式应被拦截。"""
        with pytest.raises(ExpressionValidationError):
            validate_expression("")

    def test_non_string_expression(self):
        """非字符串应被拦截。"""
        with pytest.raises(ExpressionValidationError):
            validate_expression(None)
        with pytest.raises(ExpressionValidationError):
            validate_expression(123)

    def test_returns_stripped_expression(self):
        """返回值应为 strip 后的表达式。"""
        result = validate_expression("  Mean($close, 5)  ")
        assert result == "Mean($close, 5)"

    def test_is_safe_expression_valid(self):
        """is_safe_expression 对合法表达式返回 True。"""
        assert is_safe_expression("Mean($close, 5)") is True

    def test_is_safe_expression_import(self):
        """is_safe_expression 对 import 返回 False。"""
        assert is_safe_expression("import os") is False

    def test_is_safe_expression_negative_ref(self):
        """is_safe_expression 对负数 Ref 返回 False。"""
        assert is_safe_expression("Ref($close, -1)") is False

    def test_is_safe_expression_invalid_field(self):
        """is_safe_expression 对非法字段返回 False。"""
        assert is_safe_expression("$hack") is False

    def test_is_safe_expression_syntax_error(self):
        """is_safe_expression 对语法错误返回 False。"""
        assert is_safe_expression("Mean($close, ") is False

    def test_nested_valid_expression(self):
        """复合嵌套表达式应通过。"""
        expr = "Corr(Mean($close, 5), Std($volume, 10), 20)"
        result = validate_expression(expr)
        assert result == expr

    def test_mixed_arithmetic_valid(self):
        """混合算术运算应通过。"""
        expr = "($close - Mean($close, 20)) / Std($close, 20)"
        result = validate_expression(expr)
        assert result == expr
