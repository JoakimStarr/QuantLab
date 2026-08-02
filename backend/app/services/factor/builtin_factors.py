"""内置基础因子库：动量/反转/波动/换手/规模/估值等经典因子。"""
from app.services.factor.library import add_factor
from app.services.factor.expression import validate_expression

# (name, expression, description)
BUILTIN_FACTORS = [
    # 动量/反转：用正数 Ref 取过去价格（负数 Ref 是未来数据，会导致 look-ahead bias）
    ("momentum_20d", "$close / Ref($close, 20) - 1", "20日动量"),
    ("momentum_60d", "$close / Ref($close, 60) - 1", "60日动量"),
    ("reversal_5d", "-1 * ($close / Ref($close, 5) - 1)", "5日反转"),
    ("volatility_20d", "Std($close / Ref($close, 1) - 1, 20)", "20日波动率"),
    ("volatility_60d", "Std($close / Ref($close, 1) - 1, 60)", "60日波动率"),
    ("turnover_20d", "Mean($volume / Ref($close, 1) / 10000, 20)", "20日平均换手(近似)"),
    ("volume_ratio_5_20", "Mean($volume, 5) / Mean($volume, 20)", "量比5/20"),
    ("amplitude_20d", "Mean(($high - $low) / $close, 20)", "20日平均振幅"),
    ("rsi_like_20d", "Mean(Greater($close / Ref($close, 1) - 1, 0), 20) / Std($close / Ref($close, 1) - 1, 20)", "类RSI强弱"),  # noqa: E501

    ("price_ma_div_20", "$close / Mean($close, 20) - 1", "价格偏离20日均线"),
    ("price_ma_div_60", "$close / Mean($close, 60) - 1", "价格偏离60日均线"),
    ("max_drawback_20", "$close / Max($close, 20) - 1", "相对20日高点的回撤"),
]


async def seed_builtin_factors() -> dict:
    """初始化内置因子（已存在则跳过）。"""
    from sqlalchemy import select
    from app.core.database import async_session
    from app.models.factor import Factor

    added = 0
    skipped = 0
    failed = 0
    for name, expr, desc in BUILTIN_FACTORS:
        try:
            validate_expression(expr)
        except Exception:
            failed += 1
            continue
        async with async_session() as session:
            exists = await session.execute(
                select(Factor).where(Factor.name == name, Factor.category == "builtin")
            )
            if exists.scalar_one_or_none() is not None:
                skipped += 1
                continue
        await add_factor(name, expr, category="builtin", description=desc, skip_validation=True)
        added += 1
    return {"added": added, "skipped": skipped, "failed": failed, "total": len(BUILTIN_FACTORS)}
