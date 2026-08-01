"""基于 PyPortfolioOpt 的组合优化器"""
import logging
import numpy as np
import pandas as pd
from typing import Optional, Dict
from pypfopt import EfficientFrontier, risk_models, expected_returns

logger = logging.getLogger(__name__)


def max_sharpe_portfolio(prices_df, weight_bounds=(0, 0.05)):
    """使用 PyPortfolioOpt 计算最大夏普比率组合

    Args:
        prices_df: 历史价格 DataFrame，index=日期，columns=股票代码
        weight_bounds: 权重上下限

    Returns:
        Dict[str, float]: 权重字典
    """
    mu = expected_returns.mean_historical_return(prices_df)
    S = risk_models.CovarianceShrinkage(prices_df).ledoit_wolf()
    ef = EfficientFrontier(mu, S, weight_bounds=weight_bounds)
    weights = ef.max_sharpe()
    return weights


def min_volatility_portfolio(prices_df, weight_bounds=(0, 0.05)):
    """使用 PyPortfolioOpt 计算最小波动率组合

    Args:
        prices_df: 历史价格 DataFrame，index=日期，columns=股票代码
        weight_bounds: 权重上下限

    Returns:
        Dict[str, float]: 权重字典
    """
    mu = expected_returns.mean_historical_return(prices_df)
    S = risk_models.CovarianceShrinkage(prices_df).ledoit_wolf()
    ef = EfficientFrontier(mu, S, weight_bounds=weight_bounds)
    weights = ef.min_volatility()
    return weights


def optimize_portfolio(
    scores: pd.Series,
    industry_map: Optional[Dict[str, str]] = None,
    method: str = "mean_variance",
    max_weight: float = 0.05,
    max_industry_exposure: float = 0.20,
    risk_aversion: float = 0.5,
    tracking_error_limit: Optional[float] = None,
    turnover_limit: Optional[float] = None,
    prev_weights: Optional[pd.Series] = None,
    benchmark_weights: Optional[pd.Series] = None,
) -> pd.Series:
    """使用 PyPortfolioOpt 优化组合权重

    Args:
        scores: 因子打分（截面值，index=股票代码）
        industry_map: {stock_code: industry_name}，传入后行业暴露约束才生效
        method: max_sharpe / min_volatility / max_return / mean_variance
        max_weight: 单股权重上限
        max_industry_exposure: 行业暴露上限
        risk_aversion: 均值-方差目标的风险厌恶系数（越大越保守）
        tracking_error_limit: 跟踪误差上限（PyPortfolioOpt 不支持，仅保留接口）
        turnover_limit: 换手率上限（PyPortfolioOpt 不支持，仅保留接口）
        prev_weights: 上一期权重（PyPortfolioOpt 不支持，仅保留接口）
        benchmark_weights: 基准权重（PyPortfolioOpt 不支持，仅保留接口）

    Returns:
        优化后的权重 Series
    """
    codes = scores.index.tolist()
    n = len(codes)
    if n == 0:
        return pd.Series(dtype=float)

    if tracking_error_limit is not None or turnover_limit is not None:
        logger.warning("PyPortfolioOpt 不支持跟踪误差/换手率约束，已忽略")

    # 归一化打分作为预期收益
    mu = scores.values.astype(float)
    mu = (mu - mu.mean()) / (mu.std() + 1e-8)
    mu_series = pd.Series(mu, index=codes)

    # 创建对角协方差矩阵（仅截面数据，无法估计协方差）
    S = pd.DataFrame(np.eye(n), index=codes, columns=codes)

    # 构建有效前沿
    ef = EfficientFrontier(mu_series, S, weight_bounds=(0, max_weight))

    # 行业暴露约束
    if industry_map:
        industries = sorted(set(industry_map.values()))
        for ind in industries:
            ind_stocks = [codes.index(c) for c in codes if industry_map.get(c) == ind]
            if ind_stocks:
                ef.add_constraint(lambda w, idx=ind_stocks: sum(w[idx]) <= max_industry_exposure)

    # 选择优化方法
    try:
        if method == "max_sharpe":
            weights = ef.max_sharpe()
        elif method == "min_volatility":
            weights = ef.min_volatility()
        elif method == "max_return":
            weights = ef.max_quadratic_utility(risk_aversion=0)
        else:  # mean_variance
            weights = ef.max_quadratic_utility(risk_aversion=risk_aversion)

        weights_series = pd.Series(weights, index=codes)
        weights_series[weights_series < 1e-6] = 0
        weights_series = weights_series / weights_series.sum()
        logger.info("PyPortfolioOpt 优化成功: method=%s", method)
        return weights_series
    except Exception as e:
        logger.warning("PyPortfolioOpt 优化失败: %s，回退到等权", e)
        return _equal_weight(scores)


def _equal_weight(scores: pd.Series) -> pd.Series:
    """等权回退"""
    codes = scores.index.tolist()
    n = len(codes)
    if n == 0:
        return pd.Series(dtype=float)
    return pd.Series(1.0 / n, index=codes)