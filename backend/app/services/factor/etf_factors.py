"""ETF 因子集：OHLCV-only 的因子模板，供 ETF 标的池评价/挖掘。

设计：
- ETF bin 只有 open/high/low/close/volume/amount/change/tradable/factor，
  无 pe_ttm/is_st/turn 等股票字段，因此因子全部基于 OHLCV/量/额/涨跌幅。
- 覆盖动量、均线偏离、波动率、量价、涨跌形态等维度（约 28 个）。
- 导入 category='etf'；评价需在 ETF 池（etf_all）上通过"补算指标"触发。
"""

ETF_FACTOR_EXPRESSIONS: list[dict] = [
    # ---- 动量 ----
    {"name": "ETF_ROC5", "expr": "$close/Ref($close, 5) - 1", "category": "etf", "description": "5日动量"},
    {"name": "ETF_ROC10", "expr": "$close/Ref($close, 10) - 1", "category": "etf", "description": "10日动量"},
    {"name": "ETF_ROC20", "expr": "$close/Ref($close, 20) - 1", "category": "etf", "description": "20日动量"},
    {"name": "ETF_ROC60", "expr": "$close/Ref($close, 60) - 1", "category": "etf", "description": "60日动量"},
    {"name": "ETF_RET_MOM5", "expr": "Mean($change, 5)", "category": "etf", "description": "5日均涨跌幅"},
    {"name": "ETF_MOM_ACCEL", "expr": "Mean($change, 5) - Mean($change, 20)", "category": "etf", "description": "动量加速（短长均涨幅差）"},  # noqa: E501
    # ---- 均线偏离/趋势 ----
    {"name": "ETF_MA_BIAS5", "expr": "$close/Mean($close, 5) - 1", "category": "etf", "description": "5日均线偏离"},
    {"name": "ETF_MA_BIAS20", "expr": "$close/Mean($close, 20) - 1", "category": "etf", "description": "20日均线偏离"},
    {"name": "ETF_MA_BIAS60", "expr": "$close/Mean($close, 60) - 1", "category": "etf", "description": "60日均线偏离"},
    {"name": "ETF_MA5_MA20", "expr": "Mean($close, 5)/Mean($close, 20) - 1", "category": "etf", "description": "5/20均线关系（金叉/死叉强度）"},  # noqa: E501
    {"name": "ETF_MA20_MA60", "expr": "Mean($close, 20)/Mean($close, 60) - 1", "category": "etf", "description": "20/60均线关系"},  # noqa: E501
    # ---- 波动率 ----
    {"name": "ETF_RVOL5", "expr": "Std($close, 5)/$close", "category": "etf", "description": "5日收益波动率"},
    {"name": "ETF_RVOL20", "expr": "Std($close, 20)/$close", "category": "etf", "description": "20日收益波动率"},
    {"name": "ETF_ATR5", "expr": "Mean($high - $low, 5)/$close", "category": "etf", "description": "5日平均振幅"},
    {"name": "ETF_AMP5", "expr": "Mean(($high - $low)/$low, 5)", "category": "etf", "description": "5日振幅比"},
    # ---- 价格形态 ----
    {"name": "ETF_CLOSE_POS", "expr": "($close - $low)/($high - $low + 1e-12)", "category": "etf", "description": "收盘在日内区间位置"},  # noqa: E501
    {"name": "ETF_UP_SHADOW", "expr": "($high - Greater($open, $close))/($high - $low + 1e-12)", "category": "etf", "description": "上影线占比"},  # noqa: E501
    {"name": "ETF_DN_SHADOW", "expr": "(Less($open, $close) - $low)/($high - $low + 1e-12)", "category": "etf", "description": "下影线占比"},  # noqa: E501
    {"name": "ETF_HIGH_BIAS20", "expr": "$high/Mean($high, 20) - 1", "category": "etf", "description": "20日高点偏离"},
    {"name": "ETF_LOW_BIAS20", "expr": "$low/Mean($low, 20) - 1", "category": "etf", "description": "20日低点偏离"},
    # ---- 量价 ----
    {"name": "ETF_VOL_RATIO5", "expr": "$volume/Mean($volume, 5)", "category": "etf", "description": "5日量比"},
    {"name": "ETF_VOL_RATIO20", "expr": "$volume/Mean($volume, 20)", "category": "etf", "description": "20日量比"},
    {"name": "ETF_AMT_RATIO5", "expr": "$amount/Mean($amount, 5)", "category": "etf", "description": "5日成交额比"},
    {"name": "ETF_VOL_TREND", "expr": "Mean($volume, 5)/Mean($volume, 20)", "category": "etf", "description": "量能趋势（短长量比）"},  # noqa: E501
    {"name": "ETF_PRICE_VOL_CORR", "expr": "Corr($change, $volume, 20)", "category": "etf", "description": "20日量价相关性"},
    # ---- 涨跌结构 ----
    {"name": "ETF_UP_DAYS14", "expr": "Mean(Greater($change, 0), 14)", "category": "etf", "description": "14日上涨占比（近似RSI）"},  # noqa: E501
    {"name": "ETF_MAX_RET5", "expr": "Max($change, 5)", "category": "etf", "description": "5日最大单日涨幅"},
    {"name": "ETF_MIN_RET5", "expr": "Min($change, 5)", "category": "etf", "description": "5日最大单日跌幅"},
]


async def seed_etf_factors() -> dict:
    """将 ETF 因子集批量导入因子库（category='etf'）。

    幂等：category='etf' 已存在则返回 already_imported。
    只导入不评价——评价需在 ETF 池（etf_all）上通过"补算指标"触发。
    """
    from sqlalchemy import select
    from app.core.database import async_session
    from app.models.factor import Factor
    from app.services.factor.library import add_factors_batch

    async with async_session() as session:
        existing = await session.execute(
            select(Factor.id).where(Factor.category == "etf").limit(1)
        )
        if existing.scalars().first():
            return {
                "ok": True, "count": 0, "already_imported": True,
                "message": "ETF 因子集已导入，无需重复操作",
            }

    created = await add_factors_batch(
        [
            {
                "name": f["name"], "expression": f["expr"],
                "category": "etf", "description": f["description"],
            }
            for f in ETF_FACTOR_EXPRESSIONS
        ],
    )
    return {
        "ok": True, "count": len(created),
        "created": [f["name"] for f in created],
        "message": f"已导入 {len(created)} 个 ETF 因子（请在 ETF 池上补算指标）",
    }
