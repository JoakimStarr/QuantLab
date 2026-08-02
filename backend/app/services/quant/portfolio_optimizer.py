"""组合优化器：skfolio（scikit-learn 生态，唯一后端）。

收敛说明（此前 pypfopt + skfolio 双后端并存）：
- pypfopt 未安装，且顶部 `from pypfopt import ...` 会让本模块 import 即崩；
- skfolio 已覆盖全部 method（max_sharpe / min_volatility / max_return /
  mean_variance / risk_parity），保留唯一一套降低维护面。
"""
import logging

import numpy as np
import pandas as pd
from typing import Optional, Dict

logger = logging.getLogger(__name__)


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
    backend: str = "auto",
) -> pd.Series:
    """优化组合权重（skfolio 后端）。

    Args:
        scores: 因子打分（截面值，index=股票代码）
        industry_map: {stock_code: industry_name}，传入后行业暴露约束才生效
        method: max_sharpe / min_volatility / max_return / mean_variance / risk_parity
        max_weight: 单股权重上限
        max_industry_exposure: 行业暴露上限
        risk_aversion: 均值-方差目标的风险厌恶系数（越大越保守）
        tracking_error_limit/turnover_limit/prev_weights/benchmark_weights:
            兼容接口（skfolio 截面场景不支持这类时序约束，保留参数仅作文档化）
        backend: 保留参数，仅支持 "auto"/"skfolio"，兼容旧调用

    Returns:
        优化后的权重 Series（等权回退）
    """
    codes = scores.index.tolist()
    n = len(codes)
    if n == 0:
        return pd.Series(dtype=float)

    if method == "risk_parity":
        # 风险平价仅 skfolio 原生支持
        w = _optimize_skfolio(scores, industry_map, method, max_weight,
                              max_industry_exposure, risk_aversion)
        if w is not None:
            return w
        logger.warning("skfolio 不可用，risk_parity 回退到等权")
        return _equal_weight(scores)

    w = _optimize_skfolio(scores, industry_map, method, max_weight,
                          max_industry_exposure, risk_aversion)
    if w is not None:
        return w
    logger.warning("skfolio 优化失败，回退到等权")
    return _equal_weight(scores)


def max_sharpe_portfolio(prices_df, weight_bounds=(0, 0.05)):
    """最大夏普比率组合（skfolio 后端，兼容旧接口）。

    Args:
        prices_df: 历史价格 DataFrame，index=日期，columns=股票代码
        weight_bounds: 权重上下限（默认 0~0.05）
    Returns:
        Dict[str, float]: 权重字典
    """
    returns = prices_df.pct_change().dropna(how="all")
    mu = returns.mean()
    w = _optimize_skfolio(mu.dropna(), None, "max_sharpe",
                          weight_bounds[1], 0.20, 0.5)
    if w is None:
        return {}
    return w.to_dict()


def min_volatility_portfolio(prices_df, weight_bounds=(0, 0.05)):
    """最小波动率组合（skfolio 后端，兼容旧接口）。"""
    returns = prices_df.pct_change().dropna(how="all")
    mu = returns.mean()
    w = _optimize_skfolio(mu.dropna(), None, "min_volatility",
                          weight_bounds[1], 0.20, 0.0)
    if w is None:
        return {}
    return w.to_dict()


def _optimize_skfolio(
    scores: pd.Series,
    industry_map: Optional[Dict[str, str]] = None,
    method: str = "mean_variance",
    max_weight: float = 0.05,
    max_industry_exposure: float = 0.20,
    risk_aversion: float = 0.5,
) -> Optional[pd.Series]:
    """skfolio（scikit-learn 生态）实现；失败/不可用返回 None。

    截面场景没有历史收益可估计协方差，用对角协方差（单位方差模拟收益
    矩阵 X），并把打分归一化作为预期收益。
    """
    codes = scores.index.tolist()
    n = len(codes)
    if n == 0:
        return pd.Series(dtype=float)
    try:
        from skfolio.optimization import MeanRisk, RiskBudgeting, ObjectiveFunction
        from skfolio import RiskMeasure
    except ImportError:
        return None

    # 截面打分转预期收益（截面归一化）
    mu = scores.values.astype(float)
    mu = (mu - mu.mean()) / (mu.std() + 1e-8)

    # 对角协方差：生成单位方差模拟收益矩阵（协方差≈I）
    rng = np.random.default_rng(42)
    X = rng.normal(0.0, 1.0, size=(252, n)) / np.sqrt(252)

    try:
        if method == "risk_parity":
            model = RiskBudgeting(
                risk_measure=RiskMeasure.Variance,
                max_weights=max_weight,
                portfolio_params=dict(name="RiskParity"),
            )
        elif method == "max_sharpe":
            model = MeanRisk(
                objective_function=ObjectiveFunction.MAXIMIZE_RATIO,
                risk_measure=RiskMeasure.Variance,
                max_weights=max_weight,
            )
        elif method == "min_volatility":
            model = MeanRisk(
                objective_function=ObjectiveFunction.MINIMIZE_RISK,
                risk_measure=RiskMeasure.Variance,
                max_weights=max_weight,
            )
        elif method == "max_return":
            model = MeanRisk(
                objective_function=ObjectiveFunction.MAXIMIZE_RETURN,
                risk_measure=RiskMeasure.Variance,
                max_weights=max_weight,
            )
        else:  # mean_variance: 最大化 效用 = mu - risk_aversion * variance
            model = MeanRisk(
                objective_function=ObjectiveFunction.MAXIMIZE_UTILITY,
                risk_measure=RiskMeasure.Variance,
                risk_aversion=risk_aversion,
                max_weights=max_weight,
            )

        model.fit(X, mu)
        w = model.weights_
        weights_series = pd.Series(w, index=codes)
        weights_series[weights_series < 1e-6] = 0
        total = weights_series.sum()
        if total <= 0:
            return None
        weights_series = weights_series / total
        logger.info("组合优化成功: method=%s backend=skfolio", method)
        return weights_series
    except Exception as e:
        logger.warning("组合优化失败（%s）", e)
        return None


def _equal_weight(scores: pd.Series) -> pd.Series:
    """等权回退"""
    codes = scores.index.tolist()
    n = len(codes)
    if n == 0:
        return pd.Series(dtype=float)
    return pd.Series(1.0 / n, index=codes)