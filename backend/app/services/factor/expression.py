"""因子表达式安全沙箱：校验 LLM/遗传规划生成的 qlib 表达式。

只允许白名单内的 qlib 算子与字段，禁止任意 Python 代码。
qlib 表达式示例：Ref($close, -20) / $close - 1
"""
import re
import ast
from app.core.config import settings

# qlib 内置算子（常用的，可按需扩展）
_QLIB_OPS = {
    "Ref", "Mean", "Std", "Var", "Max", "Min", "Sum", "Rank", "Quantile",
    "Corr", "Cov", "Delta", "Slope", "Resi", "WMA", "EMA", "MA", "RSRS",
    "Greater", "Less", "Gt", "Lt", "Ge", "Le", "Eq", "Ne",
    "Abs", "Log", "Power", "Sign", "If", "IdxMax", "IdxMin",
    "Product", "Count", "Mad", "Clip", "Range", "Floor", "Ceil",
    "All", "Any", "Pair", "Bias", "Div", "Sub", "Add", "Mul",
}

# 允许的字段（$开头）
_QLIB_FIELDS = {"$open", "$close", "$high", "$low", "$volume", "$amount", "$factor", "$change"}

# 严格禁止的标识符（词边界匹配，避免 "os" 误伤 "close"）
_FORBIDDEN_WORDS = {"import", "exec", "eval", "open", "lambda", "os", "sys", "subprocess", "globals", "locals", "getattr", "setattr"}
_FORBIDDEN_SUBSTR = {"__", "compile", "builtins"}


class ExpressionValidationError(ValueError):
    pass


def validate_expression(expr: str) -> str:
    """校验因子表达式安全性，返回清洗后的表达式。"""
    if not expr or not isinstance(expr, str):
        raise ExpressionValidationError("表达式为空")
    expr = expr.strip()
    if len(expr) > 500:
        raise ExpressionValidationError("表达式过长（>500字符）")

    # 禁止危险关键字
    lower = expr.lower()
    for kw in _FORBIDDEN_SUBSTR:
        if kw in lower:
            raise ExpressionValidationError(f"表达式包含禁止关键字: {kw}")
    for kw in _FORBIDDEN_WORDS:
        if re.search(rf"\b{re.escape(kw)}\b", lower):
            raise ExpressionValidationError(f"表达式包含禁止关键字: {kw}")

    # 提取所有标识符（函数名与变量名）
    # $field 形式
    fields = set(re.findall(r"\$[a-zA-Z_]+", expr))
    for f in fields:
        if f not in _QLIB_FIELDS:
            raise ExpressionValidationError(f"不允许的字段: {f}（允许: {sorted(_QLIB_FIELDS)}）")

    # 函数名/标识符（不含 $field）：先剔除 $field 再提取，避免误取字段名片段
    expr_no_fields = re.sub(r"\$[a-zA-Z_]+", " ", expr)
    identifiers = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", expr_no_fields))

    # look-ahead bias 防护：禁止负数 Ref（Ref 负数=未来数据）
    # 匹配 Ref(...,  -N) 形式
    negative_refs = re.findall(r"Ref\s*\([^,]+,\s*-\d+", expr, re.IGNORECASE)
    if negative_refs:
        raise ExpressionValidationError(
            f"禁止负数 Ref（未来数据，导致 look-ahead bias）: {negative_refs[:2]}"
        )
    allowed = settings.mining.get("llm", {}).get("allowed_ops", [])
    allowed_set = set(allowed) if allowed else _QLIB_OPS
    # 合并配置白名单与内置算子
    allowed_set = allowed_set | _QLIB_OPS

    for ident in identifiers:
        # 跳过纯数字、True/False
        if ident in ("True", "False", "true", "false", "nan", "NaN", "inf"):
            continue
        if ident not in allowed_set:
            raise ExpressionValidationError(
                f"不允许的标识符: {ident}（如为 qlib 算子请加入 allowed_ops 白名单）"
            )

    # 语法结构校验：$field 非合法 Python 标识符，替换后再 AST 解析
    expr_for_ast = re.sub(r"\$([a-zA-Z_]+)", r"x_\1", expr)
    try:
        tree = ast.parse(expr_for_ast, mode="eval")
    except SyntaxError as e:
        raise ExpressionValidationError(f"表达式语法错误: {e}") from e

    # 禁止属性访问/导入/赋值等危险结构
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ExpressionValidationError(f"禁止访问下划线属性: {node.attr}")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ExpressionValidationError("禁止 import 语句")

    return expr


def is_safe_expression(expr: str) -> bool:
    """探测表达式是否安全（不抛异常）。"""
    try:
        validate_expression(expr)
        return True
    except ExpressionValidationError:
        return False
