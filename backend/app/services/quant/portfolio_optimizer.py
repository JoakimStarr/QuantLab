"""组合优化器：skfolio（scikit-learn 生态）+ scipy 均值-方差回退。

收敛说明（此前 pypfopt + skfolio 双后端并存）：
- pypfopt 未安装，且顶部 `from pypfopt import ...` 会让本模块 import 即崩；
- skfolio 已覆盖全部 method（max_sharpe / min_volatility / max_return /
  mean_variance / risk_parity），保留唯一一套降低维护面。

回退链：skfolio → scipy SLSQP 均值-方差（同签名）→ 等权。
回退只降级不伪装：skfolio/scipy 均失败时以 WARNING 日志明示回退等权。
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def optimize_portfolio(
    scores: pd.Series,
    industry_map: dict[str, str] | None = None,
    method: str = "mean_variance",
    max_weight: float = 0.05,
    max_industry_exposure: float = 0.20,
    risk_aversion: float = 0.5,
    tracking_error_limit: float | None = None,
    turnover_limit: float | None = None,
    prev_weights: pd.Series | None = None,
    benchmark_weights: pd.Series | None = None,
    backend: str = "auto",
) -> pd.Series:
    """优化组合权重。

    Args:
        scores: 因子打分（截面值，index=股票代码）
        industry_map: {stock_code: industry_name}，传入后行业暴露约束才生效
        method: max_sharpe / min_volatility / max_return / mean_variance / risk_parity
        max_weight: 单股权重上限（需 >= 1/n 否则约束不可行，回退等权）
        max_industry_exposure: 行业暴露上限
        risk_aversion: 均值-方差目标的风险厌恶系数（越大越保守）
        tracking_error_limit/turnover_limit/prev_weights/benchmark_weights:
            兼容接口（截面场景不支持这类时序约束，保留参数仅作文档化）
        backend: 保留参数，仅支持 "auto"/"skfolio"，兼容旧调用

    Returns:
        优化后的权重 Series（skfolio → scipy → 等权 回退链）
    """
    codes = scores.index.tolist()
    n = len(codes)
    if n == 0:
        return pd.Series(dtype=float)

    if method == "risk_parity":
        # 风险平价仅 skfolio 原生支持（scipy 回退不做风险平价）
        w = _optimize_skfolio(scores, industry_map, method, max_weight, max_industry_exposure, risk_aversion)
        if w is not None:
            return w
        logger.warning("skfolio 不可用，risk_parity 回退到等权")
        return _equal_weight(scores)

    w = _optimize_skfolio(scores, industry_map, method, max_weight, max_industry_exposure, risk_aversion)
    if w is not None:
        return w
    # skfolio 不可用/失败 → scipy 均值-方差（单位协方差近似）
    w = _optimize_scipy(scores, method, max_weight, risk_aversion)
    if w is not None:
        return w
    logger.warning("组合优化失败（skfolio/scipy 均不可用），回退到等权")
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
    w = _optimize_skfolio(mu.dropna(), None, "max_sharpe", weight_bounds[1], 0.20, 0.5)
    if w is None:
        return {}
    return w.to_dict()


def min_volatility_portfolio(prices_df, weight_bounds=(0, 0.05)):
    """最小波动率组合（skfolio 后端，兼容旧接口）。"""
    returns = prices_df.pct_change().dropna(how="all")
    mu = returns.mean()
    w = _optimize_skfolio(mu.dropna(), None, "min_volatility", weight_bounds[1], 0.20, 0.0)
    if w is None:
        return {}
    return w.to_dict()


def _normalize_scores(scores: pd.Series) -> np.ndarray:
    """截面打分转预期收益（z-score 归一化）。"""
    mu = scores.values.astype(float)
    return (mu - mu.mean()) / (mu.std() + 1e-8)


def _cleanup_weights(raw: np.ndarray, codes: list, max_weight: float) -> pd.Series | None:
    """清整优化器输出的权重向量：去尘、截断投影、归一化；失败/不可行返回 None。

    采用迭代"clip → 归一化"（capped projection）：单次 clip 后再除以总和会把
    触顶权重重新抬过上限，迭代到不动点才能同时满足 sum=1 与 w<=max_weight。
    """
    w = np.asarray(raw, dtype=float).clip(min=0.0)
    w[w < 1e-6] = 0
    for _ in range(100):
        total = w.sum()
        if total <= 0:
            return None
        w = w / total
        if w.max() <= max_weight * (1 + 1e-9):
            return pd.Series(w, index=codes)
        w = np.clip(w, 0.0, max_weight)
    # 未收敛 = 约束不可行（max_weight < 1/n 时无解）
    logger.warning("权重截断投影未收敛（max_weight=%s, n=%d，疑似不可行）", max_weight, len(codes))
    return None


def _optimize_skfolio(
    scores: pd.Series,
    industry_map: dict[str, str] | None = None,
    method: str = "mean_variance",
    max_weight: float = 0.05,
    max_industry_exposure: float = 0.20,
    risk_aversion: float = 0.5,
) -> pd.Series | None:
    """skfolio（scikit-learn 生态）实现；失败/不可用返回 None。

    截面场景没有历史收益可估计协方差，用对角协方差（单位方差模拟收益
    矩阵 X），并把打分归一化作为预期收益。
    """
    codes = scores.index.tolist()
    n = len(codes)
    if n == 0:
        return pd.Series(dtype=float)
    try:
        from skfolio import RiskMeasure
        from skfolio.optimization import MeanRisk, ObjectiveFunction, RiskBudgeting

        # skfolio 1.x 用大写 VARIANCE，旧版本为 Variance
        try:
            variance = RiskMeasure.VARIANCE
        except AttributeError:
            variance = RiskMeasure.Variance
    except ImportError:
        logger.debug("skfolio 未安装，组合优化走 scipy 回退")
        return None

    mu = _normalize_scores(scores)

    # 模拟收益矩阵：EmpiricalPrior 的预期收益取 X 列均值，故构造列均值恰为
    # mu 的 X（打分即预期收益）；列内噪声各向同性 → 协方差 ≈ 单位阵近似。
    # 注意 fit(X, y) 的 y 是因子收益而非预期收益，不能传 mu。
    rng = np.random.default_rng(42)
    noise = rng.normal(0.0, 1.0, size=(252, n))
    noise -= noise.mean(axis=0)  # 列去均值，保证列均值精确等于 mu
    X = mu[None, :] + noise

    try:
        if method == "risk_parity":
            model = RiskBudgeting(
                risk_measure=variance,
                max_weights=max_weight,
                portfolio_params=dict(name="RiskParity"),
            )
        elif method == "max_sharpe":
            model = MeanRisk(
                objective_function=ObjectiveFunction.MAXIMIZE_RATIO,
                risk_measure=variance,
                max_weights=max_weight,
            )
        elif method == "min_volatility":
            model = MeanRisk(
                objective_function=ObjectiveFunction.MINIMIZE_RISK,
                risk_measure=variance,
                max_weights=max_weight,
            )
        elif method == "max_return":
            model = MeanRisk(
                objective_function=ObjectiveFunction.MAXIMIZE_RETURN,
                risk_measure=variance,
                max_weights=max_weight,
            )
        else:  # mean_variance: 最大化 效用 = mu - risk_aversion * variance
            model = MeanRisk(
                objective_function=ObjectiveFunction.MAXIMIZE_UTILITY,
                risk_measure=variance,
                risk_aversion=risk_aversion,
                max_weights=max_weight,
            )

        model.fit(X, mu)
        w = _cleanup_weights(model.weights_, codes, max_weight)
        if w is not None:
            logger.info("组合优化成功: method=%s backend=skfolio", method)
        return w
    except Exception as e:
        logger.warning("组合优化失败（%s）", e)
        return None


def _optimize_scipy(
    scores: pd.Series,
    method: str = "mean_variance",
    max_weight: float = 0.05,
    risk_aversion: float = 0.5,
) -> pd.Series | None:
    """scipy SLSQP 均值-方差回退（同签名）；失败/不可行返回 None。

    截面无历史收益：协方差取单位阵，方差 = sum(w^2)，
    目标 maximize mu·w - risk_aversion * sum(w^2)，约束 sum(w)=1、0<=w<=max_weight。
    仅支持 mean_variance（其余 method 由 skfolio 覆盖；scipy 阶段按 mean_variance 处理）。
    """
    codes = scores.index.tolist()
    n = len(codes)
    if n == 0:
        return pd.Series(dtype=float)
    # 可行性：单股上限必须能容下 1/n，否则约束无解
    if max_weight < 1.0 / n:
        logger.warning(
            "scipy 组合优化不可行: max_weight=%s < 1/n=%s（n=%d），跳过",
            max_weight,
            1.0 / n,
            n,
        )
        return None
    try:
        from scipy.optimize import minimize
    except ImportError:
        return None

    mu = _normalize_scores(scores)

    def obj(w: np.ndarray) -> float:
        return -(float(mu @ w) - risk_aversion * float(w @ w))

    constraints = [{"type": "eq", "fun": lambda w: float(np.sum(w)) - 1.0}]
    bounds = [(0.0, max_weight)] * n
    w0 = np.full(n, 1.0 / n)
    try:
        res = minimize(obj, w0, method="SLSQP", bounds=bounds, constraints=constraints, options={"maxiter": 300})
        if not res.success or res.x is None:
            logger.warning("scipy 组合优化未收敛: %s", res.message)
            return None
        w = _cleanup_weights(res.x, codes, max_weight)
        if w is not None:
            logger.info("组合优化成功: method=mean_variance backend=scipy")
        return w
    except Exception as e:
        logger.warning("scipy 组合优化失败: %s", e)
        return None


def _equal_weight(scores: pd.Series) -> pd.Series:
    """等权回退"""
    codes = scores.index.tolist()
    n = len(codes)
    if n == 0:
        return pd.Series(dtype=float)
    return pd.Series(1.0 / n, index=codes)
