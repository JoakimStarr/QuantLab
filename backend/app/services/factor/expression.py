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
_FORBIDDEN_WORDS = {"import", "exec", "eval", "lambda", "os", "sys", "subprocess", "globals", "locals", "getattr", "setattr"}
# "open" removed from word ban; detected via open() call pattern below to avoid blocking $open field
_FORBIDDEN_SUBSTR = {"__", "compile", "builtins", "automl", "autogluon"}


class ExpressionValidationError(ValueError):
    pass


def _const_value(node) -> float | None:
    """返回 AST 常量节点的数值（支持一元 +/-），无法判定返回 None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp):
        v = _const_value(node.operand)
        if v is None:
            return None
        return -v if isinstance(node.op, ast.USub) else v
    return None


def _find_negative_ref(tree) -> list[str]:
    """遍历 AST 找 Ref(..., 负常量)（未来数据，look-ahead bias），返回命中描述。

    AST 方式可正确处理嵌套参数（如 Ref(Mean($close,5), -1)），弥补正则漏检。
    """
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Ref":
            if len(node.args) >= 2:
                v = _const_value(node.args[1])
                if v is not None and v < 0:
                    try:
                        desc = f"Ref(..., {int(v) if v == int(v) else v})"
                    except Exception:
                        desc = "Ref(..., 负数)"
                    hits.append(desc)
    return hits


def validate_expression(expr: str, max_length: int = 2000) -> str:
    """校验因子表达式安全性，返回清洗后的表达式。"""
    if not expr or not isinstance(expr, str):
        raise ExpressionValidationError("表达式为空")
    expr = expr.strip()
    if len(expr) > max_length:
        raise ExpressionValidationError(f"表达式过长（>{max_length}字符）")

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

    # Detect open() function calls (separate from word ban to allow $open field)
    if re.search(r'\bopen\s*\(', lower):
        raise ExpressionValidationError("expression contains forbidden open() call")

    # 函数名/标识符（不含 $field）：先剔除 $field 再提取，避免误取字段名片段
    expr_no_fields = re.sub(r"\$[a-zA-Z_]+", " ", expr)
    identifiers = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", expr_no_fields))

    # look-ahead bias 防护见下方 AST 阶段（正则无法处理嵌套 Ref 参数）
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

    # look-ahead bias 防护：禁止负数 Ref（Ref 负数=未来数据），AST 可处理嵌套参数
    negative_refs = _find_negative_ref(tree)
    if negative_refs:
        raise ExpressionValidationError(
            f"禁止负数 Ref（未来数据，导致 look-ahead bias）: {negative_refs[:2]}"
        )

    return expr


def is_safe_expression(expr: str) -> bool:
    """探测表达式是否安全（不抛异常）。"""
    try:
        validate_expression(expr)
        return True
    except ExpressionValidationError:
        return False


def check_lookahead(expr: str) -> None:
    """仅检查负数 Ref（look-ahead bias），不做白名单校验。

    用于 load_factor_values 等执行入口的防御性检查：即使表达式绕过了创建时的
    完整校验（skip_validation），也保证不会加载未来数据。非标准表达式（AutoML/
    TextSentiment 占位符）语法解析失败时直接放行，交由上游处理。
    """
    if not expr:
        return
    expr_for_ast = re.sub(r"\$([a-zA-Z_]+)", r"x_\1", expr)
    try:
        tree = ast.parse(expr_for_ast, mode="eval")
    except SyntaxError:
        return
    hits = _find_negative_ref(tree)
    if hits:
        raise ExpressionValidationError(
            f"禁止负数 Ref（未来数据，导致 look-ahead bias）: {hits[:2]}"
        )
