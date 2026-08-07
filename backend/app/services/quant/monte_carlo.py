"""蒙特卡罗模拟：回测指标 bootstrap 置信区间 + 因子 IC 置换检验。

回答两类"结果是不是靠运气"的问题：
1. 回测指标：对历史日收益做 block bootstrap（stationary bootstrap，保留序列
   自相关），重采样 N 次重算核心指标，得到每个指标的分布与置信区间。
2. 因子 IC 显著性：逐日截面内打乱因子值（置换检验），构建 IC 零分布，
   得到非参数 p-value（补充现有 Newey-West t 检验）。

实现要点：
- bootstrap 用 `arch.bootstrap.StationaryBootstrap`（已装 8.0）。
- 指标直接对 numpy 数组用 empyrical 计算（不依赖 datetime 索引——重采样后
  存在重复日期，`portfolio_report._empyrical_metrics` 里的 resample 会失效）。
- 置换检验用 `np.lexsort((random, day_id))` 做组内打乱 + `np.bincount` 向量化。
- 所有函数为模块级、纯 numpy/pandas，可 pickle 进 run_cpu 进程池。
"""
import logging
from collections import OrderedDict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# bootstrap 覆盖的核心指标（与 portfolio.py / portfolio_report.py 口径一致）
_METRICS = (
    "sharpe",
    "sortino",
    "calmar",
    "cagr",
    "annual_volatility",
    "max_drawdown",
    "win_rate",
)

# 最少观测数：过短序列 bootstrap 无意义（分位噪声极大）
_MIN_OBS = 30


def metric_values(returns) -> dict:
    """从日收益数组计算核心指标（供 bootstrap 重采样与点估计复用）。

    Args:
        returns: 日收益序列（list / np.ndarray / pd.Series，数值即可）。

    Returns:
        {sharpe, sortino, calmar, cagr, annual_volatility, max_drawdown, win_rate}，
        无法计算的指标为 None。
    """
    from empyrical import (
        annual_return,
        annual_volatility,
        calmar_ratio,
        max_drawdown,
        sharpe_ratio,
        sortino_ratio,
    )

    r = np.asarray(returns, dtype=float)
    out = {}
    for key, func, kwargs in (
        ("sharpe", sharpe_ratio, {"period": "daily"}),
        ("sortino", sortino_ratio, {"period": "daily"}),
        ("cagr", annual_return, {"period": "daily"}),
        ("annual_volatility", annual_volatility, {"period": "daily"}),
    ):
        try:
            out[key] = float(func(r, **kwargs))
        except Exception:  # noqa: BLE001 - empyrical 在退化输入下抛错，忽略
            out[key] = None
    try:
        out["calmar"] = float(calmar_ratio(r))
    except Exception:  # noqa: BLE001
        out["calmar"] = None
    try:
        out["max_drawdown"] = float(max_drawdown(r))
    except Exception:  # noqa: BLE001
        out["max_drawdown"] = None
    out["win_rate"] = float((r > 0).mean()) if len(r) else None
    return out


def bootstrap_metric_ci(returns, n_iter: int = 1000, block: int = 20,
                        seed: int = 42, ci_level: float = 0.9) -> dict:
    """日收益 block bootstrap：核心指标分布与置信区间。

    Args:
        returns: 日收益序列（数值即可，无需 datetime 索引）。
        n_iter: bootstrap 重采样次数。
        block: stationary bootstrap 的平均块长（交易日）。
        seed: 随机种子（可复现）。
        ci_level: 置信水平（默认 0.9 → [5%, 95%]）。

    Returns:
        {
          "n_obs", "n_iter", "block", "ci_level",
          "metrics": {m: {point, lo, hi, median, p5, p95, std}},
          "sharpe_samples": [...],   # Sharpe 重采样分布（供前端直方图）
          "error"?: 样本不足时的说明
        }
    """
    x = np.asarray(returns, dtype=float)
    x = x[~np.isnan(x)]
    n = int(len(x))
    if n < _MIN_OBS:
        return {
            "n_obs": n, "n_iter": 0, "block": block, "ci_level": ci_level,
            "metrics": {}, "sharpe_samples": [],
            "error": f"收益样本不足（{n} < {_MIN_OBS} 个交易日），无法 bootstrap",
        }

    point = metric_values(x)
    lo_q = 100.0 * (1 - ci_level) / 2
    hi_q = 100.0 * (1 + ci_level) / 2

    samples = {m: [] for m in _METRICS}
    try:
        from arch.bootstrap import StationaryBootstrap

        bs = StationaryBootstrap(block, x, seed=seed)
        for data in bs.bootstrap(n_iter):
            # arch 直接产出重采样后的数据数组（float64），非索引
            resampled = np.asarray(data[0][0], dtype=float)
            vals = metric_values(resampled)
            for m in _METRICS:
                v = vals.get(m)
                if v is not None and np.isfinite(v):
                    samples[m].append(float(v))
    except ImportError:
        # arch 未安装时降级：iid bootstrap（保留自相关能力丢失，但结构可用）
        logger.warning("arch 未安装，bootstrap 降级为 iid 重采样")
        rng = np.random.default_rng(seed)
        for _ in range(n_iter):
            idx = rng.integers(0, n, size=n)
            vals = metric_values(x[idx])
            for m in _METRICS:
                v = vals.get(m)
                if v is not None and np.isfinite(v):
                    samples[m].append(float(v))

    metrics = {}
    for m in _METRICS:
        arr = np.asarray(samples[m])
        if len(arr) == 0:
            metrics[m] = {"point": point.get(m), "lo": None, "hi": None,
                          "median": None, "p5": None, "p95": None, "std": None}
            continue
        metrics[m] = {
            "point": point.get(m),
            "lo": float(np.percentile(arr, lo_q)),
            "hi": float(np.percentile(arr, hi_q)),
            "median": float(np.percentile(arr, 50)),
            "p5": float(np.percentile(arr, lo_q)),
            "p95": float(np.percentile(arr, hi_q)),
            "std": float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0,
        }

    return {
        "n_obs": n,
        "n_iter": n_iter,
        "block": block,
        "ci_level": ci_level,
        "metrics": metrics,
        "sharpe_samples": samples["sharpe"],
    }


def permutation_ic_test(factor_df: pd.DataFrame, label_df: pd.DataFrame,
                        n_permutations: int = 500, alpha: float = 0.05,
                        seed: int = 42) -> dict:
    """因子 IC 置换检验（非参数显著性，补充 Newey-West t 检验）。

    零假设：因子值与未来收益无关。每个交易日内打乱因子值（保留截面结构与
    时序结构、破坏因子-收益关联），重算"逐日截面 Pearson IC 的均值"（与
    `factor_eval.compute_ic` 口径一致），重复 N 次得到零分布；真实 IC 与
    零分布比较得双尾 p-value = (1 + #(|IC_perm| ≥ |IC_obs|)) / (1 + N)。

    向量化：`np.lexsort((random, day_id))` 实现组内（日内）打乱；
    按日统计用 `np.bincount`，每轮 O(n)。

    Args:
        factor_df: MultiIndex (datetime, instrument) 因子值，列 "factor"。
        label_df: MultiIndex (datetime, instrument) 前向收益，列 "label"。
        n_permutations: 置换次数。
        alpha: 显著性水平。
        seed: 随机种子（可复现）。

    Returns:
        {ic_obs, perm_mean, perm_std, p_value, significant, n_permutations,
         perm_ci, seed, note}；数据不足时指标为 None、significant=False。
    """
    from app.services.quant.factor_eval import _to_alphalens_factor_data

    factor_data = _to_alphalens_factor_data(factor_df, label_df)
    if factor_data is None or factor_data.empty:
        return {"ic_obs": None, "perm_mean": None, "perm_std": None, "p_value": None,
                "significant": False, "n_permutations": 0, "perm_ci": None, "seed": seed,
                "note": "因子/标签数据为空，无法置换检验"}

    dates = factor_data.index.get_level_values("date")
    day_ids, _ = pd.factorize(dates)
    day_ids = day_ids.astype(np.int64)
    factor = factor_data["factor"].to_numpy(dtype=float)
    label = factor_data["1D"].to_numpy(dtype=float)
    n_days = int(day_ids.max()) + 1

    # 预计算标签侧按日统计（每轮只重算 factor 侧）
    n_obs_day = np.bincount(day_ids, minlength=n_days).astype(float)
    sum_l = np.bincount(day_ids, weights=label, minlength=n_days)
    sum_l2 = np.bincount(day_ids, weights=label * label, minlength=n_days)

    def _daily_mean_ic(f_vals: np.ndarray) -> float:
        sum_f = np.bincount(day_ids, weights=f_vals, minlength=n_days)
        sum_f2 = np.bincount(day_ids, weights=f_vals * f_vals, minlength=n_days)
        sum_fl = np.bincount(day_ids, weights=f_vals * label, minlength=n_days)
        num = sum_fl - sum_f * sum_l / n_obs_day
        denom = np.sqrt((sum_f2 - sum_f ** 2 / n_obs_day) * (sum_l2 - sum_l ** 2 / n_obs_day))
        valid = (n_obs_day >= 2) & (denom > 0)
        corr = np.where(valid, num / np.where(valid, denom, 1.0), np.nan)
        return float(np.nanmean(corr))

    ic_obs = _daily_mean_ic(factor)
    if not np.isfinite(ic_obs) or n_days < 2:
        return {"ic_obs": ic_obs if np.isfinite(ic_obs) else None,
                "perm_mean": None, "perm_std": None, "p_value": None,
                "significant": False, "n_permutations": 0, "perm_ci": None, "seed": seed,
                "note": "有效交易日不足，无法置换检验"}

    rng = np.random.default_rng(seed)
    n = len(factor)
    perm_ics = np.empty(n_permutations)
    count = 0
    for i in range(n_permutations):
        # 组内（日内）置换：按 (day_id, random) 排序，日内因子值随机重排
        order = np.lexsort((rng.random(n), day_ids))
        perm_ics[i] = _daily_mean_ic(factor[order])
        if abs(perm_ics[i]) >= abs(ic_obs):
            count += 1

    p_value = (1 + count) / (1 + n_permutations)
    return {
        "ic_obs": ic_obs,
        "perm_mean": float(np.mean(perm_ics)),
        "perm_std": float(np.std(perm_ics, ddof=1)) if n_permutations > 1 else 0.0,
        "p_value": float(p_value),
        "significant": bool(p_value < alpha),
        "n_permutations": int(n_permutations),
        "perm_ci": [float(np.percentile(perm_ics, 2.5)),
                    float(np.percentile(perm_ics, 97.5))],
        "seed": seed,
        "note": f"逐日截面打乱因子值 {n_permutations} 次，双尾 p-value",
    }


# ---------- 蒙特卡罗结果进程内 LRU 缓存（同 result_id + 参数命中免重算） ----------

_MC_CACHE_MAX = 128
_MC_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()


def mc_cache_get(key: tuple) -> dict | None:
    """LRU 读取蒙特卡罗计算结果缓存（命中后提升优先级）。"""
    val = _MC_CACHE.get(key)
    if val is not None:
        _MC_CACHE.move_to_end(key)
    return val


def mc_cache_set(key: tuple, value: dict) -> None:
    """LRU 写入蒙特卡罗计算结果缓存（超限淘汰最久未用）。"""
    _MC_CACHE[key] = value
    _MC_CACHE.move_to_end(key)
    if len(_MC_CACHE) > _MC_CACHE_MAX:
        _MC_CACHE.popitem(last=False)


def mc_cache_clear() -> None:
    """清空缓存（测试用）。"""
    _MC_CACHE.clear()
