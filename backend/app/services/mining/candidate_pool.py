"""因子候选池：预置经典技术指标与常用因子表达式。

用于挖掘时给 LLM 提供候选参考（从中挑选/变体），提高命中率，
也作为符号回归的初始候选。
"""
from __future__ import annotations

# 候选因子池：{名称: qlib 表达式}
# 风格分组，便于按需取用
CANDIDATE_POOL: dict[str, dict[str, str]] = {
    "momentum": {
        "mom_5": "$close/Ref($close,5)-1",
        "mom_10": "$close/Ref($close,10)-1",
        "mom_20": "$close/Ref($close,20)-1",
        "mom_60": "$close/Ref($close,60)-1",
        "mom_slope": "Slope($close,20)",
        "mom_accel": "Delta($close/Ref($close,5)-1,5)",
        "wma_mom": "WMA($close,20)/Ref(WMA($close,20),5)-1",
        "ema_mom": "EMA($close,5)/EMA($close,20)-1",
    },
    "reversal": {
        "rev_5": "-Rank(Delta($close,5))",
        "rev_10": "-Rank(Delta($close,10))",
        "rev_3": "-Rank(Delta($close,3))",
        "rev_20": "Mean($close,5)/Mean($close,20)-1",
    },
    "volatility": {
        "vol_20": "Std($close,20)/Mean($close,20)",
        "vol_10": "Std($close,10)/Mean($close,10)",
        "vol_60": "Std($close,60)/Mean($close,60)",
        "range_20": "(Max($high,20)-Min($low,20))/Mean($close,20)",
        "range_10": "(Max($high,10)-Min($low,10))/Mean($close,10)",
        "vol_ratio": "Std($close,10)/Std($close,60)-1",
    },
    "volume_price": {
        "vp_corr_10": "Corr($close,$volume,10)",
        "vp_corr_20": "Corr($close,$volume,20)",
        "vp_corr_5": "Corr($close,$volume,5)",
        "vp_amount_vol": "Rank($amount)/Rank($volume)",
        "vp_vol_mom": "Mean($volume,5)/Mean($volume,20)-1",
        "vp_change_vol": "$change*Rank($volume)",
        "vp_amount_change": "Rank($amount)*$change",
    },
    "turnover": {
        "turn_rank": "Rank($turn)",
        "turn_5": "Mean($turn,5)",
        "turn_20": "Mean($turn,20)",
        "turn_abn": "Rank($turn)-Rank(Mean($turn,20))",
        "turn_change": "Delta($turn,5)/Mean($turn,20)",
        "turn_close": "Corr($close,$turn,10)",
    },
    "valuation": {
        "pe_inv": "-Rank($pe_ttm)",
        "pb_inv": "-Rank($pb_mrq)",
        "pe_pb": "Rank($pb_mrq)/Rank($pe_ttm)",
        "pe_change": "$pe_ttm/Ref($pe_ttm,20)-1",
        "pb_change": "$pb_mrq/Ref($pb_mrq,20)-1",
        "pe_5m": "Rank(-$pe_ttm)*Rank(Mean($close,5)/Mean($close,20))",
    },
    "technical": {
        "rsi_14": "Mean(Max($close-Ref($close,1),0),14)/Mean(Abs($close-Ref($close,1)),14)*100",
        "boll_20": "($close-Mean($close,20))/(2*Std($close,20))",
        "macd_hist": "EMA($close,12)-EMA($close,26)",
        "gold_cross": "If(EMA($close,5)>EMA($close,20),1,-1)",
        "williams_r": "(Max($high,14)-$close)/(Max($high,14)-Min($low,14))",
        "cci_20": "($close-Mean($close,20))/(0.015*Mean(Abs($close-Mean($close,20)),20))",
    },
    "price_level": {
        "close_20pos": "Rank($close/Mean($close,20)-1)",
        "high_20pos": "$close/Max($high,20)-1",
        "low_20pos": "$close/Min($low,20)-1",
        "close_60pos": "Rank($close/Mean($close,60)-1)",
    },
}

# 风格 -> 池中 key 列表
STYLE_KEYS = {
    "momentum": list(CANDIDATE_POOL["momentum"].keys()),
    "reversal": list(CANDIDATE_POOL["reversal"].keys()),
    "volatility": list(CANDIDATE_POOL["volatility"].keys()),
    "volume_price": list(CANDIDATE_POOL["volume_price"].keys()),
    "turnover": list(CANDIDATE_POOL["turnover"].keys()),
    "valuation": list(CANDIDATE_POOL["valuation"].keys()),
    "technical": list(CANDIDATE_POOL["technical"].keys()),
    "price_level": list(CANDIDATE_POOL["price_level"].keys()),
}


def get_candidates(style: str = None, n: int = 10) -> list[dict]:
    """按风格获取候选因子，返回 [{"name", "expression"}, ...]。

    Args:
        style: momentum/reversal/volatility/volume_price/turnover/valuation/technical/price_level
               None 时从所有风格挑选（覆盖多种风格）
        n: 返回数量上限
    """
    if style and style in STYLE_KEYS:
        keys = STYLE_KEYS[style]
        pool = CANDIDATE_POOL[style]
    else:
        # 无风格或未知风格：跨风格轮流取，保证多样性
        keys = []
        for s in CANDIDATE_POOL.values():
            keys.extend(s.keys())
        pool = {k: v for s in CANDIDATE_POOL.values() for k, v in s.items()}

    selected = []
    for k in keys:
        if len(selected) >= n:
            break
        selected.append({"name": k, "expression": pool[k]})
    return selected


def get_candidates_for_template(template: dict, n: int = 8) -> list[dict]:
    """根据挖掘模板返回候选（模板 key 尽量匹配风格）。"""
    style_hint = {
        "momentum": "momentum",
        "volatility": "volatility",
        "volume_price": "volume_price",
        "reversal": "reversal",
        "sentiment": "turnover",
        "valuation": "valuation",
    }
    style = style_hint.get(template.get("key", ""))
    return get_candidates(style, n=n)


def format_candidates_for_prompt(candidates: list[dict]) -> str:
    """把候选池格式化为 prompt 文本，供 LLM 参考。"""
    lines = ["【候选因子参考（可从中挑选、组合或改进）】"]
    for c in candidates:
        lines.append(f"  - {c['name']}: {c['expression']}")
    return "\n".join(lines)
