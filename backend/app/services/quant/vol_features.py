"""波动率与特征工程：ARCH GARCH(1,1) 条件波动率 + tsfresh 时序特征提取。

用途：
- compute_garch_vol：GARCH(1,1) 条件波动率（比滚动 Std 对波动聚集更敏感，
  作为波动率因子研究的补充代理）
- extract_tsfresh_features：从面板序列批量提取时序特征（候选特征池）

重要约束：
- 这些特征由 Python 侧计算，不是 qlib bin 字段。若要进入符号回归挖掘的
  终端池（gplearn 终端必须可翻译为 qlib 表达式），需先转储为 qlib 字段，
  或改用 qlib 原生可表达的特征（见 symbolic.py 中 _BASE_FEATURES 扩展）。
- compute_garch_vol 默认在全样本上拟合（in-sample），条件波动率含轻微
  前视偏差，仅用于研究/EDA；严谨场景请用 point_in_time=True 的滚动拟合
  （速度慢，慎用大面板）。
"""
import logging
from functools import lru_cache

import numpy as np
import pandas as pd
from cachetools import LRUCache

logger = logging.getLogger(__name__)

# GARCH 拟合结果缓存：key=(instrument, 序列长度, 最后日期)，避免重复拟合
_GARCH_CACHE: LRUCache = LRUCache(maxsize=512)


def compute_garch_vol(
    returns_df: pd.DataFrame,
    min_obs: int = 120,
    point_in_time: bool = False,
    use_cache: bool = True,
) -> pd.DataFrame:
    """逐标的拟合 GARCH(1,1) 并返回条件波动率序列。

    Args:
        returns_df: 日收益 DataFrame，index=日期，columns=标的代码
        min_obs: 拟合所需最少观测数（不足返回 NaN 列）
        point_in_time: True 时用扩展窗口滚动拟合（无前视，慢）；
                        False 时全样本拟合（默认，快，轻微前视）
        use_cache: 全样本模式下启用内存缓存（key 含最后日期，数据追加后自动失效）

    Returns:
        与 returns_df 同索引同列的条件波动率 DataFrame（NaN 表示拟合不足/失败）
    """
    try:
        from arch import arch_model
    except ImportError:
        logger.warning("arch 未安装，GARCH 波动率不可用")
        return pd.DataFrame(index=returns_df.index, columns=returns_df.columns, dtype=float)

    out = pd.DataFrame(index=returns_df.index, columns=returns_df.columns, dtype=float)
    for inst in returns_df.columns:
        s = pd.Series(returns_df[inst]).astype(float).dropna()
        if len(s) < min_obs:
            out[inst] = np.nan
            continue

        cache_key = (inst, len(s), s.index[-1])
        if use_cache and not point_in_time:
            cached = _GARCH_CACHE.get(cache_key)
            if cached is not None:
                out[inst] = cached.reindex(out.index)
                continue

        try:
            if point_in_time:
                vol = _garch_vol_point_in_time(s, min_obs)
            else:
                res = arch_model(s, vol="GARCH", p=1, q=1, dist="normal").fit(
                    disp="off", show_warning=False
                )
                vol = res.conditional_volatility
                vol = pd.Series(vol, index=s.index).astype(float)
                if use_cache:
                    _GARCH_CACHE[cache_key] = vol
            out[inst] = vol.reindex(out.index)
        except Exception as e:
            logger.debug("GARCH 拟合失败 %s: %s", inst, e)
            out[inst] = np.nan
    return out


def _garch_vol_point_in_time(s: pd.Series, min_obs: int) -> pd.Series:
    """扩展窗口滚动 GARCH(1,1)：t 时刻的波动率只由 [0, t] 的数据估计。

    每步重拟合一阶 GARCH，只适合小面板（样本数 × 标的小）。
    """
    from arch import arch_model

    vol = pd.Series(np.nan, index=s.index, dtype=float)
    for i in range(min_obs, len(s)):
        sub = s.iloc[:i]
        try:
            res = arch_model(sub, vol="GARCH", p=1, q=1).fit(disp="off", show_warning=False)
            cv = res.conditional_volatility
            if len(cv) > 0:
                vol.iloc[i] = cv.iloc[-1]
        except Exception:
            vol.iloc[i] = np.nan
    return vol


# tsfresh 精选特征（避免默认提取数百个特征的耗时）
_CURATED_TSFRESH_FEATURES = {
    "abs_energy": None,
    "skewness": None,
    "kurtosis": None,
    "mean_abs_change": None,
    "longest_strike_above_mean": None,
    "longest_strike_below_mean": None,
    "variance": None,
    "quantile": [{"q": 0.1}, {"q": 0.5}, {"q": 0.9}],
    "change_quantiles": [{"ql": 0.1, "qh": 0.9, "isabs": False, "f_agg": "mean"}],
    "cid_ce": [{"normalize": True}],
}


def extract_tsfresh_features(
    panel_df: pd.DataFrame,
    value_column: str = "value",
    features: dict = None,
    min_obs: int = 30,
    n_jobs: int = 1,
) -> pd.DataFrame:
    """从面板数据提取 tsfresh 时序特征。

    Args:
        panel_df: MultiIndex=(datetime, instrument) 面板，含 value_column 数值列
        value_column: 待提取特征的数值列名
        features: tsfresh kind_to_fc_parameters 字典（默认使用精选特征集）
        min_obs: 每只标的最少观测数（不足剔除）
        n_jobs: 并行数（tsfresh 默认单进程）

    Returns:
        DataFrame index=instrument，columns=特征名（feat__<name>__<params>）
    """
    try:
        from tsfresh import extract_features
    except ImportError:
        logger.warning("tsfresh 未安装，特征提取不可用")
        return pd.DataFrame()

    if panel_df.empty or value_column not in panel_df.columns:
        return pd.DataFrame()

    # reset_index 展开 (datetime, instrument)，避免与列名歧义
    df = panel_df[[value_column]].reset_index()
    if "instrument" not in df.columns or "datetime" not in df.columns:
        return pd.DataFrame()

    # 剔除观测不足的标的
    counts = df.groupby("instrument")[value_column].count()
    valid = counts[counts >= min_obs].index
    df = df[df["instrument"].isin(valid)]

    try:
        features = extract_features(
            df,
            column_id="instrument",
            column_sort="datetime",
            column_value=value_column,
            default_fc_parameters=features or _CURATED_TSFRESH_FEATURES,
            n_jobs=n_jobs,
        )
        return features.dropna(axis=1, how="all")
    except Exception as e:
        logger.warning("tsfresh 特征提取失败: %s", e)
        return pd.DataFrame()


@lru_cache(maxsize=32)
def garch_vol_cached(inst: str, start: str, end: str) -> tuple:
    """单标的 GARCH 条件波动率（带缓存，供外部直接调用）。"""
    from app.services.quant.factor_eval import load_factor_values

    df = load_factor_values("$close", start, end)
    if df is None or df.empty:
        return ()
    if inst not in df.index.get_level_values("instrument"):
        return ()
    close = df.loc[inst, "factor"].dropna()
    rets = close.pct_change().dropna()
    if len(rets) < 60:
        return ()
    vol = compute_garch_vol(pd.DataFrame({inst: rets}), min_obs=60, use_cache=False)
    series = vol[inst].dropna()
    return (tuple(series.index.strftime("%Y-%m-%d")), tuple(series.values))
