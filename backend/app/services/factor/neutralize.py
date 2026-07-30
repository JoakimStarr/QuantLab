"""因子中性化"""
import logging
import numpy as np
import pandas as pd
from typing import Optional, Dict

logger = logging.getLogger(__name__)


def market_cap_neutralize(
    factor_df: pd.DataFrame,
    factor_col: str = "factor",
    market_cap: Optional[pd.Series] = None,
) -> pd.DataFrame:
    """市值中性化：每日截面回归 factor ~ ln(market_cap)，取残差

    Args:
        factor_df: index=(datetime, instrument), columns=[factor_col, ...]
        factor_col: 因子值列名
        market_cap: 市值 Series，index=instrument。如果为None则从 market_data 获取

    Returns:
        中性化后的 DataFrame，新增 factor_col + "_neutralized" 列
    """
    if market_cap is None:
        from app.services.data.market_data import get_log_market_cap
        market_cap = get_log_market_cap()

    if market_cap.empty:
        logger.warning("市值数据为空，跳过中性化")
        factor_df[f"{factor_col}_neutralized"] = factor_df[factor_col]
        return factor_df

    ln_mcap = np.log(market_cap.astype(float))

    result = factor_df.copy()
    result[f"{factor_col}_neutralized"] = np.nan
    neut_col = f"{factor_col}_neutralized"

    # 按日期分组做截面回归（groupby 迭代避免逐日全表 mask 扫描）
    for dt, day_factor in factor_df[factor_col].groupby(level="datetime"):
        day_stocks = day_factor.index.get_level_values("instrument")
        day_mcap = ln_mcap.reindex(day_stocks)

        valid = day_factor.notna() & day_mcap.notna()
        if valid.sum() < 2:
            result.loc[day_factor.index, neut_col] = day_factor.values
            continue

        y = day_factor[valid].values
        x = day_mcap[valid].values

        # OLS: y = alpha + beta * x + epsilon
        x_mean = x.mean()
        y_mean = y.mean()
        x_var = np.sum((x - x_mean) ** 2)
        if x_var < 1e-12:
            result.loc[day_factor.index, neut_col] = day_factor.values
            continue
        beta = np.sum((x - x_mean) * (y - y_mean)) / x_var
        alpha = y_mean - beta * x_mean

        residual = day_factor.values - (alpha + beta * day_mcap.values)
        result.loc[day_factor.index, neut_col] = residual

    logger.info("市值中性化完成")
    return result


def industry_neutralize(
    factor_df: pd.DataFrame,
    industry_map: Optional[Dict[str, str]] = None,
    factor_col: str = "factor",
    market_cap: Optional[pd.Series] = None,
) -> pd.DataFrame:
    """行业+市值中性化：每日截面回归 factor ~ industry_dummies + ln(market_cap)，取残差

    Args:
        factor_df: index=(datetime, instrument), columns=[factor_col, ...]
        industry_map: {stock_code: industry_name}，为 None 时从 industry_sync 加载
        factor_col: 因子值列名
        market_cap: 市值 Series

    Returns:
        中性化后的 DataFrame
    """
    from sklearn.linear_model import LinearRegression

    # 加载行业映射
    if industry_map is None:
        from app.services.data.industry_sync import load_industry_map
        industry_map = load_industry_map()

    # 获取市值
    if market_cap is None:
        from app.services.data.market_data import get_log_market_cap
        market_cap = get_log_market_cap()

    if not industry_map:
        logger.warning("行业映射为空，仅做市值中性化")
        return market_cap_neutralize(factor_df, factor_col, market_cap)

    ln_mcap = np.log(market_cap.astype(float)) if not market_cap.empty else None

    # 所有行业
    industries = sorted(set(industry_map.values()))

    result = factor_df.copy()
    result[f"{factor_col}_neutralized"] = np.nan
    neut_col = f"{factor_col}_neutralized"

    for dt, day_factor in factor_df[factor_col].groupby(level="datetime"):
        day_stocks = day_factor.index.get_level_values("instrument")

        # 构建特征矩阵
        features = []

        # 行业 dummy（留一个作为基准，避免共线性）
        for ind in industries[:-1]:
            col = np.array([1.0 if industry_map.get(s) == ind else 0.0 for s in day_stocks])
            features.append(col)

        # 对数市值
        if ln_mcap is not None:
            features.append(ln_mcap.reindex(day_stocks).values)

        if not features:
            result.loc[day_factor.index, neut_col] = day_factor.values
            continue

        X = np.column_stack(features)
        y = day_factor.values

        # 有效数据
        valid = ~np.isnan(y)
        for f in X.T:
            valid = valid & ~np.isnan(f)

        if valid.sum() < len(features) + 2:
            result.loc[day_factor.index, neut_col] = day_factor.values
            continue

        # OLS 回归
        model = LinearRegression(fit_intercept=True)
        model.fit(X[valid], y[valid])
        residual = y.copy()
        residual[valid] = y[valid] - model.predict(X[valid])
        residual[~valid] = np.nan

        result.loc[day_factor.index, neut_col] = residual

    logger.info("行业+市值中性化完成")
    return result
