"""LLM 因子挖掘预设模板"""

# 预设挖掘模板
MINING_TEMPLATES = {
    "momentum": {
        "name": "动量因子模板",
        "description": "基于价格动量的因子挖掘，关注近期收益趋势",
        "llm_prompt": (
            "请生成 5 个动量类量化因子表达式，使用 qlib 语法。"
            "因子应基于 $close, $open, $high, $low, $volume 等基础特征。"
            "可使用 Ref, Mean, Std, Delta, Slope, WMA, EMA 等算子。"
            "重点关注 5-20 日的动量效应。"
            '返回 JSON 格式: {{"factors": [{{"name": "...", "expression": "...", "description": "..."}}]}}'
        ),
        "base_features": ["$close", "$open", "$high", "$low", "$volume"],
        "allowed_ops": ["Ref", "Mean", "Std", "Delta", "Slope", "WMA", "EMA", "Rank"],
        "ic_threshold": 0.03,
    },
    "volatility": {
        "name": "波动率因子模板",
        "description": "基于价格波动率的因子挖掘，关注风险特征",
        "llm_prompt": (
            "请生成 5 个波动率类量化因子表达式，使用 qlib 语法。"
            "因子应衡量价格或收益率的波动特征。"
            "可使用 Std, Max, Min, Corr, Cov 等算子。"
            "关注 10-60 日的波动率特征。"
            '返回 JSON 格式: {{"factors": [{{"name": "...", "expression": "...", "description": "..."}}]}}'
        ),
        "base_features": ["$close", "$high", "$low", "$volume"],
        "allowed_ops": ["Std", "Max", "Min", "Corr", "Cov", "Ref", "Mean", "Rank"],
        "ic_threshold": 0.03,
    },
    "volume_price": {
        "name": "量价因子模板",
        "description": "基于量价关系的因子挖掘，关注成交量与价格的交互",
        "llm_prompt": (
            "请生成 5 个量价关系类量化因子表达式，使用 qlib 语法。"
            "因子应结合成交量($volume)和价格($close, $high, $low)的特征。"
            "可使用 Corr, Cov, Sum, Rank, Ref 等算子。"
            "关注量价背离、放量上涨/下跌等信号。"
            '返回 JSON 格式: {{"factors": [{{"name": "...", "expression": "...", "description": "..."}}]}}'
        ),
        "base_features": ["$close", "$volume", "$high", "$low"],
        "allowed_ops": ["Corr", "Cov", "Sum", "Rank", "Ref", "Mean", "Std"],
        "ic_threshold": 0.03,
    },
    "reversal": {
        "name": "反转因子模板",
        "description": "基于价格反转的因子挖掘，关注过度反应后的修正",
        "llm_prompt": (
            "请生成 5 个反转类量化因子表达式，使用 qlib 语法。"
            "因子应捕捉短期过度反应后的反转机会。"
            "可使用 Ref, Delta, Rank, Slope, Resi 等算子。"
            "关注 3-10 日的短期反转效应。"
            '返回 JSON 格式: {{"factors": [{{"name": "...", "expression": "...", "description": "..."}}]}}'
        ),
        "base_features": ["$close", "$open", "$high", "$low"],
        "allowed_ops": ["Ref", "Delta", "Rank", "Slope", "Resi", "Mean"],
        "ic_threshold": 0.03,
    },
    "sentiment": {
        "name": "情绪因子模板",
        "description": "基于市场情绪的因子挖掘，关注波动和成交特征",
        "llm_prompt": (
            "请生成 5 个市场情绪类量化因子表达式，使用 qlib 语法。"
            "因子应反映市场情绪，如换手率、振幅、资金流向等。"
            "可使用 Sum, Mean, Std, Corr, Rank 等算子。"
            '返回 JSON 格式: {{"factors": [{{"name": "...", "expression": "...", "description": "..."}}]}}'
        ),
        "base_features": ["$close", "$volume", "$high", "$low"],
        "allowed_ops": ["Sum", "Mean", "Std", "Corr", "Rank", "Ref", "Delta"],
        "ic_threshold": 0.03,
    },
}


def get_template(template_key: str) -> dict:
    """获取模板配置"""
    tpl = MINING_TEMPLATES.get(template_key)
    if tpl:
        return {"key": template_key, **tpl}
    return None


def list_templates() -> list[dict]:
    """列出所有模板"""
    return [{"key": k, "name": v["name"], "description": v["description"]}
            for k, v in MINING_TEMPLATES.items()]
