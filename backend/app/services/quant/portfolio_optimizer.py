"""基于 CVXPy 的组合优化器"""
import logging
import numpy as np
import pandas as pd
from typing import Optional, Dict, Any

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
) -> pd.Series:
    """使用 CVXPy 优化组合权重

    Args:
        scores: 因子打分（截面值，index=股票代码）
        industry_map: {stock_code: industry_name}，传入后行业暴露约束才生效
        method: max_return / min_tracking_error / mean_variance
            （max_sharpe 作为 mean_variance 别名保留，非真正最大夏普比率，
             真正 max Sharpe 需分式规划转化，此处为均值-方差近似）
        max_weight: 单股权重上限
        max_industry_exposure: 行业暴露上限
        risk_aversion: 均值-方差目标的风险厌恶系数（越大越保守）
        tracking_error_limit: 跟踪误差上限
        turnover_limit: 换手率上限
        prev_weights: 上一期权重（用于换手率约束）
        benchmark_weights: 基准权重（用于跟踪误差）

    Returns:
        优化后的权重 Series
    """
    try:
        import cvxpy as cp
    except ImportError:
        logger.warning("cvxpy 未安装，回退到等权")
        return _equal_weight(scores)

    codes = scores.index.tolist()
    n = len(codes)
    if n == 0:
        return pd.Series(dtype=float)

    # max_sharpe 作为 mean_variance 别名（非真正 max Sharpe）
    if method == "max_sharpe":
        logger.info("method=max_sharpe 实为均值-方差近似（risk_aversion=%.2f），非真正最大夏普", risk_aversion)
        method = "mean_variance"

    # 归一化打分作为预期收益
    mu = scores.values.astype(float)
    mu = (mu - mu.mean()) / (mu.std() + 1e-8)

    # 决策变量
    w = cp.Variable(n, nonneg=True)

    # 约束列表
    constraints = [
        cp.sum(w) == 1,  # 满仓
        w <= max_weight,  # 单股权重上限
    ]

    # 行业暴露约束
    if industry_map:
        industries = sorted(set(industry_map.values()))
        for ind in industries:
            ind_mask = [1.0 if industry_map.get(c) == ind else 0.0 for c in codes]
            constraints.append(cp.sum(cp.multiply(ind_mask, w)) <= max_industry_exposure)

    # 换手率约束
    if prev_weights is not None and turnover_limit is not None:
        prev_w = prev_weights.reindex(codes).fillna(0).values
        turnover = cp.sum(cp.abs(w - prev_w))
        constraints.append(turnover <= turnover_limit)

    # 跟踪误差约束（简化：用权重偏差的 L2 范数近似）
    if benchmark_weights is not None and tracking_error_limit is not None:
        bench_w = benchmark_weights.reindex(codes).fillna(0).values
        tracking_error = cp.norm(w - bench_w, 2)
        constraints.append(tracking_error <= tracking_error_limit)

    # 目标函数
    if method == "max_return":
        objective = cp.Maximize(mu @ w)
    elif method == "min_tracking_error":
        if benchmark_weights is not None:
            bench_w = benchmark_weights.reindex(codes).fillna(0).values
            objective = cp.Minimize(cp.sum_squares(w - bench_w))
        else:
            objective = cp.Maximize(mu @ w)
    else:  # mean_variance：最大化收益 - 风险厌恶 × 方差
        objective = cp.Maximize(mu @ w - risk_aversion * cp.sum_squares(w))
    
    # 求解
    prob = cp.Problem(objective, constraints)
    try:
        prob.solve(solver=cp.OSQP, max_iter=10000)
        if w.value is not None and prob.status in ["optimal", "optimal_inaccurate"]:
            weights = pd.Series(w.value, index=codes)
            # 清理小值
            weights[weights < 1e-6] = 0
            weights = weights / weights.sum()  # 重新归一化
            logger.info("CVXPy 优化成功: method=%s, status=%s", method, prob.status)
            return weights
    except Exception as e:
        logger.warning("CVXPy 求解失败: %s，回退到等权", e)
    
    return _equal_weight(scores)


def _equal_weight(scores: pd.Series) -> pd.Series:
    """等权回退"""
    codes = scores.index.tolist()
    n = len(codes)
    if n == 0:
        return pd.Series(dtype=float)
    return pd.Series(1.0 / n, index=codes)
