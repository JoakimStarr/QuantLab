"""因子表达式安全沙箱：校验 LLM/遗传规划生成的 qlib 表达式。

只允许白名单内的 qlib 算子与字段，禁止任意 Python 代码。
qlib 表达式示例：Ref($close, -20) / $close - 1
"""
import re
import ast
from app.core.config import settings

# AST 安全沙箱配置
_ALLOWED_AST_NODES = {
    ast.Expression, ast.Call, ast.Name, ast.Constant,
    ast.BinOp, ast.UnaryOp, ast.Attribute, ast.Add, ast.Sub,
    ast.Mult, ast.Div, ast.Mod, ast.Pow, ast.USub, ast.UAdd,
    ast.Load, ast.Store,
}
_MAX_EXPR_DEPTH = 20
_MAX_EXPR_NODES = 1000  # gplearn 树形翻译会展开 max/min/if（子树复制），100 不够

# qlib 内置算子（常用的，可按需扩展）
_QLIB_OPS = {
    "Ref", "Mean", "Std", "Var", "Max", "Min", "Sum", "Rank", "Quantile",
    "Corr", "Cov", "Delta", "Slope", "Resi", "WMA", "EMA", "MA", "RSRS",
    "Greater", "Less", "Gt", "Lt", "Ge", "Le", "Eq", "Ne",
    "Abs", "Log", "Power", "Sign", "If", "IdxMax", "IdxMin",
    "Product", "Count", "Mad", "Clip", "Range", "Floor", "Ceil",
    "All", "Any", "Pair", "Bias", "Div", "Sub", "Add", "Mul",
    "Skew", "Kurt",
}

# 允许的字段（$开头）——与 baostock 回填写入 qlib bin 的字段一致
_QLIB_FIELDS = {
    "$open", "$high", "$low", "$close", "$preclose",
    "$volume", "$amount", "$turn",
    "$tradestatus", "$pct_chg", "$is_st",
    "$pe_ttm", "$pb_mrq", "$ps_ttm", "$pcf_ncf_ttm",
    "$adjustflag",
    "$change", "$tradable",
    # 宏观指标字段（macro_sync 广播写入 features/*/{field}.day.bin）
    "$pmi", "$pmi_nm", "$cpi", "$ppi", "$gdp",
    # 全球宏观指标字段（global_macro_sync 广播写入 features/*/{field}.day.bin）
    "$us_fed_rate", "$ecb_rate", "$us_cpi_yoy", "$us_unrate",
    "$us_ism_pmi", "$us_nonfarm",
    "$gold_cot_net", "$copper_cot_net", "$crude_cot_net", "$us_crude_stock",
    # 美债收益率（akshare TREASURY 已广播，补白名单）
    "$us_trsy2y", "$us_trsy10y", "$us_trsy_spread",
    # 外盘隔夜情绪因子（external_market 广播写入 features/*/{field}.day.bin）
    "$us_sp500_ret", "$us_nasdaq_ret", "$us_dow_ret", "$hk_hsi_ret",
    # 市场热度日频字段（macro_sync MARKET_*/SH_INDEX/CONG，非东财源，广播全市场同一数组）
    "$pe_mid_ttm", "$pe_tt_quant_hist", "$pe_tt_quant_10y",
    "$pe_sh", "$pb_sh", "$pb_sh_mid", "$div_yield_sh",
    "$hs300_pe_ttm", "$hs300_pe_std", "$sh_idx_close", "$sh_idx_vol", "$congestion",
    # 季频财报字段（fundamental_sync 按股 PIT 广播写入 features/*/{field}.day.bin）
    "$netprofit", "$revenue", "$netprofit_deduct", "$roe", "$roa",
    "$gross_margin", "$net_margin", "$debt_ratio", "$ocf",
    "$eps", "$bvps", "$revenue_yoy", "$netprofit_yoy", "$ocf_to_np",
    "$current_ratio", "$quick_ratio", "$equity_multiplier",
}

# 严格禁止的标识符（词边界匹配，避免 "os" 误伤 "close"）
_FORBIDDEN_WORDS = {"import", "exec", "eval", "lambda", "os",
                    "sys", "subprocess", "globals", "locals", "getattr", "setattr"}
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


class _USubRewriter(ast.NodeTransformer):
    """把一元负号 `-X` 重写为 `X * -1`（qlib 表达式不支持 unary minus）。

    LLM 常生成 `-Mean(...)`/`-$close` 这类反转因子，沙箱 AST 白名单放行
    （UnaryOp 合法），但 qlib 解析 Feature/Mean 等对象时 `-` 会抛
    `bad operand type for unary -`，导致评价全部失败。重写后 qlib 可正常计算。
    """

    def visit_UnaryOp(self, node):
        if isinstance(node.op, ast.USub):
            operand = self.visit(node.operand)
            return ast.BinOp(
                left=operand,
                op=ast.Mult(),
                right=ast.Constant(value=-1),
            )
        return self.generic_visit(node)


def _rewrite_unary_negation(expr: str) -> str:
    """将表达式中的一元负号改写为 `X * -1`，返回改写后的表达式。"""
    expr_for_ast = re.sub(r"\$([a-zA-Z_]+)", r"x_\1", expr)
    try:
        tree = ast.parse(expr_for_ast, mode="eval")
    except SyntaxError:
        return expr
    rewritten = _USubRewriter().visit(tree)
    try:
        return ast.unparse(rewritten).replace("x_", "$")
    except Exception:
        return expr


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
def _get_depth(node, current=0) -> int:
    """计算 AST 节点最大嵌套深度。"""
    if not hasattr(node, 'body') and not hasattr(node, 'operand') and not hasattr(node, 'args'):
        return current


    max_depth = current
    for child in ast.iter_child_nodes(node):
        depth = _get_depth(child, current + 1)
        max_depth = max(max_depth, depth)
    return max_depth


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
    # 同时剔除科学计数法数字（如 1e-6 的 'e'），避免误判为标识符
    expr_no_fields = re.sub(r"\$[a-zA-Z_]+", " ", expr)
    expr_no_fields = re.sub(r"\d+(\.\d+)?[eE][+-]?\d+", " ", expr_no_fields)
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

    # AST 节点类型白名单
    for node in ast.walk(tree):
        if type(node) not in _ALLOWED_AST_NODES:
            raise ExpressionValidationError(
                f"禁止的 AST 节点类型: {type(node).__name__}"
            )

    # 表达式复杂度上限
    all_nodes = list(ast.walk(tree))
    if len(all_nodes) > _MAX_EXPR_NODES:
        raise ExpressionValidationError(
            f"表达式节点数 {len(all_nodes)} 超过上限 {_MAX_EXPR_NODES}"
        )

    # 最大嵌套深度
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            depth = _get_depth(node)
            if depth > _MAX_EXPR_DEPTH:
                raise ExpressionValidationError(
                    f"表达式嵌套深度 {depth} 超过上限 {_MAX_EXPR_DEPTH}"
                )

    # look-ahead bias 防护：禁止负数 Ref（Ref 负数=未来数据），AST 可处理嵌套参数
    negative_refs = _find_negative_ref(tree)
    if negative_refs:
        raise ExpressionValidationError(
            f"禁止负数 Ref（未来数据，导致 look-ahead bias）: {negative_refs[:2]}"
        )

    # 一元负号改写：`-X` -> `X * -1`（qlib 表达式不支持 unary minus，否则评价必失败）
    return _rewrite_unary_negation(expr)


def is_safe_expression(expr: str) -> bool:
    """探测表达式是否安全（不抛异常）。"""
    try:
        validate_expression(expr)
        return True
    except ExpressionValidationError:
        return False


# 算子中文说明（前端补全展示用；未列出的算子只有名字）
_OP_DESCRIPTIONS = {
    "Ref": "过去第n期值（负数=未来数据，禁止）",
    "Mean": "窗口内均值",
    "Std": "窗口内标准差",
    "Var": "窗口内方差",
    "Max": "窗口内最大值",
    "Min": "窗口内最小值",
    "Sum": "窗口内求和",
    "Rank": "截面排名（0~1）",
    "Quantile": "分位数",
    "Corr": "两个序列相关系数",
    "Cov": "两个序列协方差",
    "Delta": "差分：x(t)-x(t-n)",
    "Slope": "窗口内线性回归斜率",
    "Resi": "窗口内线性回归残差",
    "WMA": "加权移动平均",
    "EMA": "指数移动平均",
    "MA": "简单移动平均",
    "RSRS": "阻力支撑相对强度",
    "Abs": "绝对值",
    "Log": "自然对数",
    "Power": "幂",
    "Sign": "符号函数",
    "If": "条件：If(cond, a, b)",
    "Product": "窗口内连乘",
    "Count": "窗口内非零个数",
    "Mad": "窗口内绝对偏差中位数",
    "Clip": "数值裁剪",
    "Range": "窗口内 Max-Min",
    "Floor": "向下取整",
    "Ceil": "向上取整",
    "Skew": "窗口内偏度",
    "Kurt": "窗口内峰度",
    "Greater": "大于比较",
    "Less": "小于比较",
    "Gt": "大于比较",
    "Lt": "小于比较",
    "Ge": "大于等于",
    "Le": "小于等于",
    "Eq": "等于",
    "Ne": "不等于",
    "Add": "加法",
    "Sub": "减法",
    "Mul": "乘法",
    "Div": "除法",
    "Bias": "偏离度（现价/均线-1）",
    "Pair": "配对算子",
    "IdxMax": "窗口内最大值位置",
    "IdxMin": "窗口内最小值位置",
}

# 字段中文说明（前端补全展示用；未列出的字段只有名字）
_FIELD_DESCRIPTIONS = {
    "$open": "开盘价", "$high": "最高价", "$low": "最低价", "$close": "收盘价",
    "$preclose": "昨收价", "$volume": "成交量(股)", "$amount": "成交额(元)",
    "$turn": "换手率(%)", "$tradestatus": "交易状态(1正常/0停牌)",
    "$pct_chg": "涨跌幅(%)", "$is_st": "是否ST", "$adjustflag": "复权状态",
    "$pe_ttm": "滚动市盈率", "$pb_mrq": "最近报告期市净率",
    "$ps_ttm": "滚动市销率", "$pcf_ncf_ttm": "滚动市现率",
    "$change": "日收益(小数)", "$tradable": "可交易掩码(涨跌停+ST 5%)",
    "$factor": "复权因子(恒1)",
    "$pmi": "制造业PMI", "$pmi_nm": "非制造业PMI",
    "$cpi": "CPI同比(%)", "$ppi": "PPI同比(%)", "$gdp": "GDP同比(%)",
    "$us_fed_rate": "美国联邦基金利率(%)", "$ecb_rate": "欧央行存款便利利率(%)",
    "$us_cpi_yoy": "美国CPI同比(%)", "$us_unrate": "美国失业率(%)",
    "$us_ism_pmi": "美国ISM制造业PMI", "$us_nonfarm": "美国非农就业人数(千人)",
    "$gold_cot_net": "黄金非商业净多(手)", "$copper_cot_net": "铜非商业净多(手)",
    "$crude_cot_net": "WTI原油非商业净多(手)", "$us_crude_stock": "美国商业原油库存(千桶)",
    "$us_trsy2y": "美债2年期收益率(%)", "$us_trsy10y": "美债10年期收益率(%)",
    "$us_trsy_spread": "美债期限利差10Y-2Y(%)",
    "$us_sp500_ret": "标普500隔夜收益", "$us_nasdaq_ret": "纳斯达克隔夜收益",
    "$us_dow_ret": "道琼斯隔夜收益", "$hk_hsi_ret": "恒生指数隔夜收益",
    "$pe_mid_ttm": "全A市盈率TTM中位数", "$pe_tt_quant_hist": "全A市盈率历史分位数",
    "$pe_tt_quant_10y": "全A市盈率近十年分位数", "$pe_sh": "上证平均市盈率",
    "$pb_sh": "上证平均市净率", "$pb_sh_mid": "上证市净率中位数",
    "$div_yield_sh": "上证A股股息率", "$hs300_pe_ttm": "沪深300滚动市盈率",
    "$hs300_pe_std": "沪深300静态市盈率", "$sh_idx_close": "上证指数收盘",
    "$sh_idx_vol": "上证指数成交量", "$congestion": "A股市场拥挤度",
    "$netprofit": "归母净利润(元)", "$revenue": "营业总收入(元)",
    "$netprofit_deduct": "扣非净利润(元)", "$roe": "净资产收益率(%)",
    "$roa": "总资产报酬率(%)", "$gross_margin": "毛利率(%)",
    "$net_margin": "销售净利率(%)", "$debt_ratio": "资产负债率(%)",
    "$ocf": "经营现金流量净额(元)", "$eps": "基本每股收益(元)",
    "$bvps": "每股净资产(元)", "$revenue_yoy": "营业总收入同比(%)",
    "$netprofit_yoy": "归母净利润同比(%)", "$ocf_to_np": "经营净现金/归母净利润",
    "$current_ratio": "流动比率", "$quick_ratio": "速动比率",
    "$equity_multiplier": "权益乘数",
}

# 宏观/利率等动态广播字段的类别标签（前端分组展示用）
_FIELD_CATEGORY = {
    "stock": "$open $high $low $close $preclose $volume $amount $turn "
              "$tradestatus $pct_chg $is_st $pe_ttm $pb_mrq $ps_ttm $pcf_ncf_ttm "
              "$adjustflag $change $tradable $factor",
    "macro": "$pmi $pmi_nm $cpi $ppi $gdp",
    "global_macro": "$us_fed_rate $ecb_rate $us_cpi_yoy $us_unrate $us_ism_pmi "
                     "$us_nonfarm $gold_cot_net $copper_cot_net $crude_cot_net "
                     "$us_crude_stock $us_trsy2y $us_trsy10y $us_trsy_spread",
    "external": "$us_sp500_ret $us_nasdaq_ret $us_dow_ret $hk_hsi_ret",
    "market": "$pe_mid_ttm $pe_tt_quant_hist $pe_tt_quant_10y $pe_sh $pb_sh "
              "$pb_sh_mid $div_yield_sh $hs300_pe_ttm $hs300_pe_std $sh_idx_close "
              "$sh_idx_vol $congestion",
    "financial": "$netprofit $revenue $netprofit_deduct $roe $roa $gross_margin "
                 "$net_margin $debt_ratio $ocf $eps $bvps $revenue_yoy "
                 "$netprofit_yoy $ocf_to_np $current_ratio $quick_ratio "
                 "$equity_multiplier",
}


def get_expression_schema() -> dict:
    """返回表达式白名单（算子和字段），供前端自动补全使用。

    与 validate_expression 共用 _QLIB_OPS/_QLIB_FIELDS + 配置 allowed_ops，
    保证"能补全的就能通过校验"。
    """
    allowed = settings.mining.get("llm", {}).get("allowed_ops", [])
    # 配置 allowed_ops 可能混入 $字段（字段另行补全），算子列表只保留非 $ 项
    ops = sorted(op for op in (_QLIB_OPS | set(allowed)) if not op.startswith("$"))
    fields = sorted(_QLIB_FIELDS)
    return {
        "ops": [
            {"name": op, "description": _OP_DESCRIPTIONS.get(op, "")} for op in ops
        ],
        "fields": [
            {
                "name": f,
                "description": _FIELD_DESCRIPTIONS.get(f, ""),
                "category": next(
                    (cat for cat, names in _FIELD_CATEGORY.items() if f in names.split()),
                    "other",
                ),
            }
            for f in fields
        ],
    }


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
