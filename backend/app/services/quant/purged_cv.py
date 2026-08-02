"""Purged 交叉验证与过拟合统计（基于 purgedcv 库）。

解决 de Prado 金融机器学习体系下的两类问题：

1. 标签重叠泄漏（label overlap）：
   horizon 前向收益标签使相邻样本的标签重叠，普通时序切分会泄漏信息
   → PurgedKFold：剔除训练集中与测试集标签时间窗重叠的样本（purge），
     并在训练/测试之间留出 embargo（隔离带，让滚动算子等特征"泄完气"）。

2. 过拟合量化（overfitting statistics）：
   - DSR（Deflated Sharpe Ratio）：考虑多重检验（n_trials 次挖掘尝试）后的真实夏普
   - 最小可辨记录长度（Min Track Record Length）：样本要多长才能信任该夏普
   - PBO（Probability of Backtest Overfitting）：对多候选路径矩阵估计过拟合概率

依赖：purgedcv（pip install purgedcv）。不可用时降级为 sklearn 顺序 KFold（无 purge）。
"""
import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    from purgedcv import PurgedKFold as _PurgedKFold

    _PURGEDCV_AVAILABLE = True
except ImportError:  # pragma: no cover - 环境缺依赖时降级
    _PurgedKFold = None
    _PURGEDCV_AVAILABLE = False

_DAYS_PER_TRADING_DAY = 7 / 5  # 自然日/交易日 换算（近似：周末 + 节假日余量）


def _horizon_to_timedelta(days: int) -> pd.Timedelta:
    """horizon（交易日）→ 自然日 Timedelta（purgedcv 需要 pd.Timedelta）。"""
    return pd.Timedelta(days=max(1, int(np.ceil(days * _DAYS_PER_TRADING_DAY))))


def _daily_spearman_ic(df: pd.DataFrame, min_cross_section: int = 10) -> pd.Series:
    """按日计算截面 Spearman IC（index=(datetime, instrument)）。

    df 需含 'factor' 与 'label' 两列。
    """
    if df.empty:
        return pd.Series(dtype=float)
    daily = df.groupby(level="datetime").apply(
        lambda g: g["factor"].corr(g["label"], method="spearman")
        if len(g) >= min_cross_section else np.nan,
        include_groups=False,
    ).dropna()
    return daily


def _load_merged(factor_expr: str, start: str, end: str, universe: str, horizon: int) -> pd.DataFrame:
    """加载因子与标签并合并（复用 factor_eval 数据路径，避免重复实现）。"""
    from app.services.quant.factor_eval import load_factor_values, load_label

    label_expr = f"Ref($close, -{horizon}) / $close - 1"
    factor_df = load_factor_values(factor_expr, start, end, universe)
    label_df = load_label(start, end, label_expr=label_expr, universe=universe)
    return factor_df.join(label_df, how="inner").dropna()


def purged_cv_ic(
    factor_expr: str,
    start: str,
    end: str,
    universe: str = None,
    horizon: int = 5,
    n_splits: int = 5,
    purge_horizon: int = None,
    embargo: int = None,
) -> dict:
    """Purged K-Fold 交叉验证下的因子 IC 评价。

    流程：
    1. 加载 (datetime, instrument) 面板数据，行即样本
    2. 用 PurgedKFold 划分（purge 掉标签重叠样本 + embargo 隔离带）
    3. 每折在测试集上计算每日截面 Spearman IC
    4. 汇总：各折 IC 均值/标准差/ICIR、整体一致性

    Args:
        factor_expr: qlib 因子表达式
        start/end: 评价日期范围
        universe: 股票池
        horizon: 预测周期（前向收益天数，标签重叠期）
        n_splits: 折数
        purge_horizon: purge 时间窗（交易日，默认取 horizon，即标签重叠期）
        embargo: 训练/测试间隔离带（交易日，默认取 max(horizon, 1)）

    Returns:
        {
            "method": "purged-kfold" | "sequential-kfold",
            "n_splits": int,
            "purge_horizon_days": int,
            "embargo_days": int,
            "folds": [{fold, n_train, n_test, start_date, end_date, ic_mean, ic_std, n_days}],
            "mean_fold_ic": float,   # 各折测试集日度 IC 均值再平均
            "fold_ic_std": float,    # 各折均值之间的离散度
            "fold_icir": float,      # mean_fold_ic / fold_ic_std
            "mean_ic": float,        # 所有测试日 IC 的合并均值
            "n_days": int,           # 测试日总数
            "n_total_samples": int,  # 面板样本数
            "purged": bool,
            "embargoed": bool,
        }
    """
    purge_days = purge_horizon if purge_horizon is not None else max(1, horizon)
    embargo_days = embargo if embargo is not None else max(1, horizon)

    merged = _load_merged(factor_expr, start, end, universe, horizon)
    if merged.empty:
        return {"method": "purged-kfold", "n_splits": n_splits,
                "purge_horizon_days": purge_days, "embargo_days": embargo_days,
                "folds": [], "mean_fold_ic": None, "fold_ic_std": None,
                "fold_icir": None, "mean_ic": None, "n_days": 0,
                "n_total_samples": 0, "purged": False, "embargoed": False,
                "note": "无有效数据（因子/标签缺失或 join 后为空）"}

    pred_times = merged.index.get_level_values("datetime")
    eval_offset = _horizon_to_timedelta(purge_days)
    eval_times = pred_times + eval_offset

    purged = False
    embargoed = False
    if _PurgedKFold is not None:
        try:
            kf = _PurgedKFold(
                n_splits=n_splits,
                prediction_times=pred_times,
                evaluation_times=eval_times,
                purge_horizon=eval_offset,
                embargo=_horizon_to_timedelta(embargo_days),
            )
            purged = True
            embargoed = True
        except Exception as e:
            logger.warning("purgedcv 划分失败（%s），降级为顺序 KFold", e)
            from sklearn.model_selection import KFold

            kf = KFold(n_splits=n_splits, shuffle=False)
    else:
        from sklearn.model_selection import KFold

        kf = KFold(n_splits=n_splits, shuffle=False)

    folds = []
    test_ic_all = []

    for fold_i, (train_idx, test_idx) in enumerate(kf.split(merged)):
        test_df = merged.iloc[test_idx]
        if test_df.empty:
            continue
        test_ic = _daily_spearman_ic(test_df)
        if test_ic.empty:
            continue
        test_ic_all.append(test_ic)
        folds.append({
            "fold": fold_i,
            "n_train": int(len(train_idx)),
            "n_test": int(len(test_idx)),
            "start_date": str(test_ic.index.min().date()),
            "end_date": str(test_ic.index.max().date()),
            "ic_mean": round(float(test_ic.mean()), 4) if len(test_ic) else None,
            "ic_std": round(float(test_ic.std()), 4) if len(test_ic) > 1 else None,
            "n_days": len(test_ic),
        })

    if not test_ic_all:
        return {"method": "purged-kfold" if purged else "sequential-kfold",
                "n_splits": n_splits, "purge_horizon_days": purge_days,
                "embargo_days": embargo_days, "folds": folds,
                "mean_fold_ic": None, "fold_ic_std": None, "fold_icir": None,
                "mean_ic": None, "n_days": 0, "n_total_samples": int(len(merged)),
                "purged": purged, "embargoed": embargoed,
                "note": "所有折的测试集均无有效 IC"}

    fold_means = [f["ic_mean"] for f in folds if f["ic_mean"] is not None]
    combined = pd.concat(test_ic_all)
    mean_fold_ic = float(np.mean(fold_means)) if fold_means else None
    fold_ic_std = float(np.std(fold_means)) if len(fold_means) > 1 else 0.0
    fold_icir = float(mean_fold_ic / fold_ic_std) if mean_fold_ic is not None and fold_ic_std > 0 else None

    return {
        "method": "purged-kfold" if purged else "sequential-kfold",
        "n_splits": n_splits,
        "purge_horizon_days": purge_days,
        "embargo_days": embargo_days,
        "folds": folds,
        "mean_fold_ic": round(mean_fold_ic, 4) if mean_fold_ic is not None else None,
        "fold_ic_std": round(fold_ic_std, 4),
        "fold_icir": round(fold_icir, 4) if fold_icir is not None else None,
        "mean_ic": round(float(combined.mean()), 4) if len(combined) else None,
        "n_days": int(len(combined)),
        "n_total_samples": int(len(merged)),
        "purged": purged,
        "embargoed": embargoed,
    }


def overfitting_statistics(
    returns=None,
    ic_series=None,
    n_trials: int = 10,
    bars_per_year: int = 252,
    alpha: float = 0.05,
) -> dict:
    """过拟合统计：DSR（Deflated Sharpe Ratio）与最小可辨记录长度。

    Args:
        returns: 策略日收益序列（优先）。None 时用 ic_series 作为收益代理。
        ic_series: 日度 IC 序列（仅当 returns 为 None 时使用）。
        n_trials: 独立搜索/挖掘尝试次数（多重检验校正力度）。
        bars_per_year: 年化交易日数。
        alpha: DSR 显著性水平。

    Returns:
        {
            "dsr": float,              # Deflated Sharpe Ratio（多重检验后夏普仍显著的概率）
            "sr_star": float,          # 过拟合基准夏普（deflated benchmark）
            "sr_observed": float,      # 观测夏普（年化）
            "probabilistic_sharpe": float,  # PSR（相对 sr_star）
            "n_trials": int,
            "min_track_record": float, # 最小可辨记录长度（年，alpha=0.05）
            "n_obs": int,
            "method": "dsr" | "ic-proxy",
        }
    """
    if returns is not None and len(np.asarray(returns, dtype=float)) >= 30:
        series = np.asarray(pd.Series(returns).dropna(), dtype=float)
        method = "dsr"
    elif ic_series is not None and len(np.asarray(ic_series, dtype=float)) >= 30:
        series = np.asarray(pd.Series(ic_series).dropna(), dtype=float)
        method = "ic-proxy"
    else:
        return {"dsr": None, "sr_star": None, "sr_observed": None,
                "probabilistic_sharpe": None, "n_trials": n_trials,
                "min_track_record": None, "n_obs": 0, "method": "insufficient-data"}

    n_obs = len(series)
    try:
        from purgedcv import deflated_sharpe_ratio_full, min_track_record_length

        sr_obs = float(np.mean(series) / (np.std(series) + 1e-12)) * np.sqrt(bars_per_year)
        # Sharpe 方差估计（PSR 公式，按观测频率）
        skew = float(pd.Series(series).skew())
        kurt = float(pd.Series(series).kurtosis())
        var_sharpe = (1 - skew * (sr_obs / np.sqrt(bars_per_year))
                      + ((kurt - 1) / 4) * (sr_obs / np.sqrt(bars_per_year)) ** 2) / n_obs
        var_sharpe = max(var_sharpe, 1e-12)

        diag = deflated_sharpe_ratio_full(series, n_trials=int(n_trials),
                                          var_sharpe=var_sharpe, bars_per_year=bars_per_year)
        mtr = min_track_record_length(observed_sharpe=sr_obs, target_sharpe=0.0,
                                      alpha=alpha, skew=skew, kurtosis=kurt)
        # min_track_record 为 inf 时表示该收益序列即使在无穷样本下也达不到显著
        mtr_val = None if mtr is None or not np.isfinite(mtr) else float(mtr)
        return {
            "dsr": round(float(diag.dsr), 4),
            "sr_star": round(float(diag.sr_star), 4),
            "sr_observed": round(float(sr_obs), 4),
            # DSR 即相对 deflated 基准（sr_star）的 PSR
            "probabilistic_sharpe": round(float(diag.dsr), 4),
            "min_track_record": round(mtr_val, 4) if mtr_val is not None else None,
            "n_trials": int(n_trials),
            "n_obs": int(n_obs),
            "alpha": alpha,
            "method": method,
        }
    except Exception as e:
        logger.warning("过拟合统计计算失败（%s），退化为无时刻校正的近似 DSR", e)
        try:
            from scipy.stats import norm

            sr_per = sr_obs / np.sqrt(bars_per_year)
            z = sr_per * np.sqrt(n_obs)
            approx = float(norm.cdf(z))
            return {
                "dsr": round(approx, 4),
                "sr_star": 0.0,
                "sr_observed": round(float(sr_obs), 4),
                "probabilistic_sharpe": round(approx, 4),
                "min_track_record": None,
                "n_trials": int(n_trials),
                "n_obs": int(n_obs),
                "alpha": alpha,
                "method": method,
                "approximation": True,
            }
        except Exception:
            return {"dsr": None, "sr_star": None, "sr_observed": None,
                    "probabilistic_sharpe": None, "n_trials": n_trials,
                    "min_track_record": None, "n_obs": int(n_obs),
                    "method": method, "error": str(e)[:200]}


def probability_of_overfitting(paths_matrix: np.ndarray, n_splits: int = 16) -> dict:
    """PBO（回测过拟合概率）估计。

    Args:
        paths_matrix: 候选策略的"收益路径"矩阵，形状 (n_trials, n_periods)，
            每行是一个候选（如不同因子/参数组合）的逐期收益序列。
        n_splits: 路径切分数（PBO 算法参数）。

    Returns:
        {"pbo": float, "n_trials": int, "n_periods": int}，失败时 pbo=None。
    """
    arr = np.asarray(paths_matrix, dtype=float)
    if arr.ndim != 2 or arr.shape[0] < 2 or arr.shape[1] < 8:
        return {"pbo": None, "n_trials": int(arr.shape[0]) if arr.ndim == 2 else 0,
                "n_periods": int(arr.shape[1]) if arr.ndim == 2 else 0,
                "error": "路径矩阵需 (n_trials>=2, n_periods>=8)"}
    try:
        from purgedcv import probability_of_backtest_overfitting

        result = probability_of_backtest_overfitting(arr, n_splits=n_splits)
        return {"pbo": round(float(result.pbo), 4), "n_trials": arr.shape[0],
                "n_periods": arr.shape[1], "n_splits": n_splits}
    except Exception as e:
        logger.warning("PBO 计算失败: %s", e)
        return {"pbo": None, "n_trials": arr.shape[0], "n_periods": arr.shape[1],
                "error": str(e)[:200]}
