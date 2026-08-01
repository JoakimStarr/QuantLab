"""因子中性化"""
import logging
import numpy as np
import pandas as pd
from typing import Optional, Dict
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import OneHotEncoder
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline

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
        market_cap: 市值 Series，index=instrument。如果为 None 则从 market_data 获取

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

    model = LinearRegression(fit_intercept=True)

    for dt, day_factor in factor_df[factor_col].groupby(level="datetime"):
        day_stocks = day_factor.index.get_level_values("instrument")
        day_mcap = ln_mcap.reindex(day_stocks)

        valid = day_factor.notna() & day_mcap.notna()
        if valid.sum() < 2:
            result.loc[day_factor.index, neut_col] = day_factor.values
            continue

        y = day_factor[valid].values
        x = day_mcap[valid].values.reshape(-1, 1)
        model.fit(x, y)
        pred = model.predict(day_mcap.values.reshape(-1, 1))
        result.loc[day_factor.index, neut_col] = day_factor.values - pred

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
    if industry_map is None:
        from app.services.data.industry_sync import load_industry_map
        industry_map = load_industry_map()

    if market_cap is None:
        from app.services.data.market_data import get_log_market_cap
        market_cap = get_log_market_cap()

    if not industry_map:
        logger.warning("行业映射为空，仅做市值中性化")
        return market_cap_neutralize(factor_df, factor_col, market_cap)

    ln_mcap = np.log(market_cap.astype(float)) if not market_cap.empty else None

    # 构建 sklearn Pipeline
    use_mcap = ln_mcap is not None
    transformers = [
        ("industry", OneHotEncoder(drop="first", sparse_output=False, handle_unknown="ignore"), ["industry"]),
    ]
    if use_mcap:
        transformers.append(("market_cap", "passthrough", ["log_market_cap"]))

    preprocessor = ColumnTransformer(transformers=transformers, remainder="drop")
    pipeline = Pipeline([
        ("preprocess", preprocessor),
        ("regress", LinearRegression(fit_intercept=True)),
    ])

    result = factor_df.copy()
    result[f"{factor_col}_neutralized"] = np.nan
    neut_col = f"{factor_col}_neutralized"

    for dt, day_factor in factor_df[factor_col].groupby(level="datetime"):
        day_stocks = day_factor.index.get_level_values("instrument")

        # 构建特征 DataFrame
        feat_df = pd.DataFrame(index=day_stocks)
        feat_df["industry"] = [industry_map.get(s, "Unknown") for s in day_stocks]
        if use_mcap:
            feat_df["log_market_cap"] = ln_mcap.reindex(day_stocks).values

        y = day_factor.values
        valid = ~np.isnan(y)
        for col in feat_df.columns:
            valid = valid & ~np.isnan(feat_df[col].values.astype(float))

        if valid.sum() < len(feat_df.columns) + 2:
            result.loc[day_factor.index, neut_col] = day_factor.values
            continue

        X = feat_df.loc[valid]
        y_valid = y[valid]
        pipeline.fit(X, y_valid)
        pred = pipeline.predict(feat_df)
        result.loc[day_factor.index, neut_col] = y - pred

    logger.info("行业+市值中性化完成")
    return result