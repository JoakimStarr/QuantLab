"""因子中性化（逐日 PIT）。

A4 修复：市值中性化改为**逐日**使用 PIT 市值（pe_ttm × 按公告日前向填充的净利润，
见 ``market_cap_pit.py``），不再把实时快照市值套到全部历史（前视偏差）。
PIT 市值不可用时**明确告警并跳过**中性化，绝不静默回退实时快照。
"""
import logging
import numpy as np
import pandas as pd
from typing import Optional, Dict
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import OneHotEncoder
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline

logger = logging.getLogger(__name__)


def _load_log_mcap(factor_df: pd.DataFrame) -> Optional[pd.Series]:
    """加载 PIT log 市值；失败返回 None（调用方告警后跳过）。"""
    try:
        from app.services.factor.market_cap_pit import load_pit_log_market_cap
        return load_pit_log_market_cap(factor_df)
    except Exception as e:  # noqa: BLE001
        logger.warning("PIT 市值加载异常，跳过中性化: %s", str(e)[:160])
        return None


def _align_log_mcap(log_mcap: Optional[pd.Series],
                    index: pd.MultiIndex) -> Optional[pd.Series]:
    """把 (datetime, instrument) 的 log 市值对齐到因子索引的层级顺序。"""
    if log_mcap is None or log_mcap.empty:
        return None
    names = list(index.names)
    if list(log_mcap.index.names) != names:
        try:
            log_mcap = log_mcap.reorder_levels(names)
        except (KeyError, ValueError):
            logger.warning("PIT 市值索引层级不匹配 %s != %s，跳过中性化",
                           list(log_mcap.index.names), names)
            return None
    return log_mcap


def _log_mcap_or_skip(factor_df: pd.DataFrame,
                      market_cap: Optional[pd.Series]) -> Optional[pd.Series]:
    """解析出对齐后的 log 市值 Series；不可用返回 None。"""
    log_mcap = market_cap if market_cap is not None else _load_log_mcap(factor_df)
    if log_mcap is None or getattr(log_mcap, "empty", True):
        return None
    return _align_log_mcap(log_mcap, factor_df.index)


def market_cap_neutralize(
    factor_df: pd.DataFrame,
    factor_col: str = "factor",
    market_cap: Optional[pd.Series] = None,
) -> pd.DataFrame:
    """市值中性化：逐日截面回归 factor ~ ln(market_cap(t))，取残差

    Args:
        factor_df: index=(datetime, instrument), columns=[factor_col, ...]
        factor_col: 因子值列名
        market_cap: PIT log 市值 Series，index=(datetime, instrument)。为 None 时
            自动从 market_cap_pit 加载；不可用则告警并跳过（不回退实时快照）。

    Returns:
        中性化后的 DataFrame，新增 factor_col + "_neutralized" 列
    """
    result = factor_df.copy()
    neut_col = f"{factor_col}_neutralized"
    result[neut_col] = np.nan

    ln_mcap = _log_mcap_or_skip(factor_df, market_cap)
    if ln_mcap is None:
        logger.warning("PIT 市值不可用，跳过市值中性化（保留原始因子值）")
        result[neut_col] = result[factor_col]
        return result

    model = LinearRegression(fit_intercept=True)
    for _dt, day_factor in factor_df[factor_col].groupby(level="datetime"):
        day_mcap = ln_mcap.reindex(day_factor.index)

        valid = day_factor.notna() & day_mcap.notna()
        if valid.sum() < 2:
            result.loc[day_factor.index, neut_col] = day_factor.values
            continue

        y = day_factor[valid].values
        x = day_mcap[valid].values.reshape(-1, 1)
        model.fit(x, y)
        # 只用训练到的有效样本预测；缺失市值的股票保持原始值（不套用拟合）
        pred = model.predict(day_mcap.fillna(day_mcap.mean()).values.reshape(-1, 1))
        pred = np.where(day_mcap.notna().values, pred, 0.0)
        result.loc[day_factor.index, neut_col] = day_factor.values - pred

    logger.info("市值中性化完成（PIT 逐日）")
    return result


def industry_neutralize(
    factor_df: pd.DataFrame,
    industry_map: Optional[Dict[str, str]] = None,
    factor_col: str = "factor",
    market_cap: Optional[pd.Series] = None,
) -> pd.DataFrame:
    """行业+市值中性化：逐日截面回归 factor ~ industry_dummies + ln(market_cap(t))

    Args:
        factor_df: index=(datetime, instrument), columns=[factor_col, ...]
        industry_map: {stock_code: industry_name}，为 None 时从 industry_sync 加载
        factor_col: 因子值列名
        market_cap: PIT log 市值 Series，index=(datetime, instrument)。为 None 时
            自动加载；不可用则仅做行业中性化（告警，不回退实时快照）。

    Returns:
        中性化后的 DataFrame
    """
    if industry_map is None:
        from app.services.data.industry_sync import load_industry_map
        industry_map = load_industry_map()

    result = factor_df.copy()
    neut_col = f"{factor_col}_neutralized"
    result[neut_col] = np.nan

    if not industry_map:
        logger.warning("行业映射为空，退化为市值中性化")
        return market_cap_neutralize(factor_df, factor_col, market_cap)

    ln_mcap = _log_mcap_or_skip(factor_df, market_cap)
    if ln_mcap is None:
        logger.warning("PIT 市值不可用，仅做行业中性化（不回退实时快照）")

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

    for _dt, day_factor in factor_df[factor_col].groupby(level="datetime"):
        idx = day_factor.index
        instruments = idx.get_level_values("instrument")

        feat_df = pd.DataFrame(index=instruments)
        feat_df["industry"] = [industry_map.get(s, "Unknown") for s in instruments]
        if use_mcap:
            feat_df["log_market_cap"] = ln_mcap.reindex(idx).values

        y = day_factor.values
        valid = ~np.isnan(y)
        for col in feat_df.columns:
            valid = valid & ~pd.isna(feat_df[col].values)

        # 需要的自由度：行业哑变量较多，保持原有 len(cols)+2 的保守下限
        if valid.sum() < len(feat_df.columns) + 2:
            result.loc[idx, neut_col] = day_factor.values
            continue

        X = feat_df.loc[valid]
        y_valid = y[valid]
        pipeline.fit(X, y_valid)
        pred = pipeline.predict(feat_df.fillna(0.0))
        result.loc[idx, neut_col] = y - pred

    logger.info("行业+市值中性化完成（PIT 逐日市值）")
    return result
