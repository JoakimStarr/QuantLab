"""AI 策略相关 prompt 模板。"""


def build_strategy_gen_prompt(
    factors: list[dict],
    correlation_hint: str = None,
    constraints: str = None,
) -> list[dict]:
    """构建「AI 生成策略」的对话消息。

    Args:
        factors: 已达标因子列表 [{id, name, expression, ic, icir, category, description}]
        correlation_hint: 可选的相关性提示文本（如 "factor A 与 B 相关性 0.82 较高"）
        constraints: 可选约束（股票池/回测区间等）
    """
    factor_lines = []
    for f in factors:
        factor_lines.append(
            f"- ID={f['id']} | {f['name']} | {f.get('category', '')} | "
            f"IC={f.get('ic'):.4f} | ICIR={f.get('icir') or '--'} | "
            f"表达式: {f.get('expression', '')}"
        )
    factor_block = "\n".join(factor_lines) or "(无可用因子)"

    corr_block = correlation_hint or "(未提供相关性数据，请按常识假设因子间相关性)"
    constraint_block = constraints or "股票池默认，回测区间默认"

    user_prompt = f"""你是量化策略专家。请根据以下因子库中已通过验证的因子，设计一个多因子选股策略。

【可选因子】
{factor_block}

【因子相关性提示】
{corr_block}

【约束】
{constraint_block}

【策略设计原则】
1. 选择 2-5 个因子：IC 较高且彼此相关性低（避免重复暴露同一风险）
2. 兼顾不同风格（如动量+反转+量价+波动），避免全选同类因子
3. topk 建议 20-100，n_drop 建议 1-10，调仓频率 day/week/month 三选一

【输出格式】严格返回 JSON：
{{
  "factor_ids": [整数ID数组],
  "topk": 50,
  "n_drop": 5,
  "rebalance_freq": "day",
  "combination_method": "equal_weight",
  "rationale": "选择这些因子及参数的理由（2-4句话）"
}}"""
    return [
        {"role": "system", "content": "你是一个严谨的量化投资组合构建专家，输出必须是合法 JSON。"},
        {"role": "user", "content": user_prompt},
    ]


def build_param_suggestion_prompt(
    factor_summary: str,
    backtest_summary: str = None,
) -> list[dict]:
    """构建「AI 参数建议」的对话消息。"""
    user_prompt = f"""你是量化策略调参专家。请为以下因子组合给出推荐的策略参数。

【因子组合】
{factor_summary}

【历史回测参考（如有）】
{backtest_summary or "(无历史回测数据)"}

【输出格式】严格返回 JSON：
{{
  "topk": 50,
  "n_drop": 5,
  "rebalance_freq": "day",
  "topk_range": [30, 80],
  "n_drop_range": [1, 10],
  "rationale": "参数选择理由"
}}"""
    return [
        {"role": "system", "content": "你是一个严谨的量化策略调参专家，输出必须是合法 JSON。"},
        {"role": "user", "content": user_prompt},
    ]


def build_review_prompt(strategy_summary: dict, metrics: dict, key_events: list) -> list[dict]:
    """构建「AI 策略复盘」的对话消息。

    Args:
        strategy_summary: 策略基本信息 {name, factor_ids, topk, ...}
        metrics: 回测绩效指标 {total_return, annual_return, sharpe, max_drawdown, ...}
        key_events: 关键事件 [{date, type, description, impact}]
    """
    metrics_block = "\n".join(f"- {k}: {v}" for k, v in (metrics or {}).items())
    events_block = "\n".join(
        f"- {e.get('date')} [{e.get('type')}] {e.get('description')} (影响: {e.get('impact')})"
        for e in (key_events or [])
    ) or "(无关键事件)"

    user_prompt = f"""你是量化策略复盘分析师。请分析以下策略的回测结果，给出专业、客观的复盘报告。

【策略信息】
{strategy_summary}

【绩效指标】
{metrics_block}

【关键事件】
{events_block}

【复盘报告要求】
1. 策略表现总评（是否跑赢基准、核心特征）
2. 收益来源分析（哪些阶段/因素贡献主要收益）
3. 风险分析（最大回撤出现在哪、原因推测、换手是否过高）
4. 优化建议（因子调整、参数、风控、交易成本）

【输出格式】严格返回 JSON：
{{
  "summary": "总评（2-3句话）",
  "strengths": ["优点1", "优点2"],
  "risks": ["风险1", "风险2"],
  "optimizations": ["优化1", "优化2"],
  "conclusion": "结论（1-2句话）"
}}"""
    return [
        {"role": "system", "content": "你是一个严谨的量化策略复盘分析师，输出必须是合法 JSON。"},
        {"role": "user", "content": user_prompt},
    ]
