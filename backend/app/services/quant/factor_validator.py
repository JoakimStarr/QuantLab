"""因子验证器：样本分割 + 滚动IC + 统计显著性 + 多样性 + 稳健性验证。

设计：
- 所有阈值可配置（通过 MiningSettings）
- 向后兼容：验证器输出 dict 与原有 evaluate_factor 输出格式兼容
- 并行友好：验证器内部计算可拆分到进程池
"""
import logging
import numpy as np
import pandas as pd
from scipy import stats
from cachetools import LRUCache
from app.core.config import settings

logger = logging.getLogger(__name__)


# ==================== 样本分割 ====================

class SampleSplitter:
    """时间序列样本分割器。

    策略：按日期顺序分割，保持截面完整性（同一日期所有股票在同一段）。
    - train: 60%（前段，用于因子发现/模型训练）
    - valid: 20%（中段，用于IC筛选和阈值判断）
    - test: 20%（后段，用于最终验证，不进筛选条件）
    """

    def __init__(self, train_ratio: float = 0.6, valid_ratio: float = 0.2):
        assert 0 < train_ratio < 1, "train_ratio 必须在 (0,1) 之间"
        assert 0 < valid_ratio < 1, "valid_ratio 必须在 (0,1) 之间"
        assert train_ratio + valid_ratio < 1, "train_ratio + valid_ratio 必须 < 1"
        self.train_ratio = train_ratio
        self.valid_ratio = valid_ratio

    def split(self, dates: list) -> dict[str, list]:
        """按日期分割，返回 {train, valid, test} 日期列表。"""
        n = len(dates)
        train_end = int(n * self.train_ratio)
        valid_end = int(n * (self.train_ratio + self.valid_ratio))
        return {
            "train": dates[:train_end],
            "valid": dates[train_end:valid_end],
            "test": dates[valid_end:],
        }

    def split_by_dates(self, actual_dates: list) -> dict[str, list]:
        """按实际交易日分割（替代按自然日），保持截面完整。

        actual_dates: 因子数据真实存在的交易日序列（升序）。
        """
        if not actual_dates:
            return {"train": [], "valid": [], "test": []}
        return self.split(list(actual_dates))

    def split_dates(self, start: str, end: str) -> dict[str, tuple[str, str]]:
        """按起止日期分割，返回 {train, valid, test} 的 (start, end) 元组。

        基于真实交易日（exchange_calendars XSHG 日历，含法定节假日），
        替代自然日近似，保证样本分割比例与真实交易天数一致。

        注意：仍仅用于无真实交易日数据的场景；
        因子评价请使用 split_by_dates（实际交易日）。
        """
        from app.services.quant.calendar_utils import get_trading_days
        try:
            trading_dates = get_trading_days(start, end)
        except Exception:
            trading_dates = pd.date_range(start=start, end=end, freq="B")
        all_dates = trading_dates.strftime("%Y-%m-%d").tolist()
        split_result = self.split(all_dates)
        result = {}
        for key, dates in split_result.items():
            if dates:
                result[key] = (dates[0], dates[-1])
            else:
                result[key] = (start, end)
        return result


# ==================== 滚动 IC 评价 ====================

class RollingICEvaluator:
    """滚动 IC 评价器。

    在已有截面 IC 序列基础上，计算滚动窗口统计量。
    """

    @staticmethod
    def evaluate(daily_ic: pd.Series, window: int = 60) -> dict:
        """计算滚动 IC 统计量。

        Args:
            daily_ic: 每日截面 IC 序列（pd.Series，index=datetime）
            window: 滚动窗口大小（交易日数）

        Returns:
            {
                "ic_mean": float,           # 全期 IC 均值
                "ic_std": float,            # 全期 IC 标准差
                "stability": float,         # 信息比率 = ic_mean / ic_std
                "positive_ratio": float,    # IC > 0 的天数占比
                "ic_first_half": float,     # 前半段 IC 均值
                "ic_second_half": float,    # 后半段 IC 均值
                "decay": float,             # 后半段 - 前半段（负值=衰减）
                "rolling_mean": list,       # 滚动均值序列
                "rolling_std": list,        # 滚动标准差序列
                "ic_series": list,          # 原始 IC 序列
            }
        """
        s = daily_ic.dropna()
        if len(s) < 2:
            return {"ic_mean": None, "ic_std": None, "stability": None,
                    "positive_ratio": None, "ic_first_half": None,
                    "ic_second_half": None, "decay": None,
                    "rolling_mean": [], "rolling_std": [], "ic_series": []}

        ic_mean = float(s.mean())
        ic_std = float(s.std()) if len(s) > 1 else None
        stability = float(ic_mean / ic_std) if ic_std and ic_std > 0 else 0.0
        positive_ratio = float((s > 0).mean())

        # 前后半段对比
        mid = len(s) // 2
        ic_first_half = float(s.iloc[:mid].mean()) if mid > 0 else ic_mean
        ic_second_half = float(s.iloc[mid:].mean()) if len(s) > mid else ic_mean
        decay = ic_second_half - ic_first_half

        # 滚动统计
        rolling_mean = s.rolling(window, min_periods=20).mean().dropna()
        rolling_std = s.rolling(window, min_periods=20).std().dropna()

        return {
            "ic_mean": round(ic_mean, 4) if ic_mean is not None else None,
            "ic_std": round(ic_std, 4) if ic_std is not None else None,
            "stability": round(stability, 4),
            "positive_ratio": round(positive_ratio, 4),
            "ic_first_half": round(ic_first_half, 4),
            "ic_second_half": round(ic_second_half, 4),
            "decay": round(decay, 4),
            "rolling_mean": [None if np.isnan(v) else float(round(v, 4)) for v in rolling_mean.values],
            "rolling_std": [None if np.isnan(v) else float(round(v, 4)) for v in rolling_std.values],
            "ic_series": [float(round(v, 4)) for v in s.values],
        }


# ==================== 统计显著性 ====================

def newey_west_t(series, lags: int = None) -> tuple:
    """Newey-West 异方差自相关一致（HAC）t 统计量。

    对重叠前向收益标签产生的自相关 IC 序列做 Bartlett 核校正，
    避免 t 检验虚高（独立样本假设不成立时 p 值系统性偏低）。

    Args:
        series: IC 序列
        lags: 自相关滞后阶数（默认取 horizon 即标签周期）

    Returns:
        (t_stat, p_value)，样本不足时返回 (None, None)
    """
    s = np.asarray(pd.Series(series).dropna(), dtype=float)
    n = len(s)
    if n < 3:
        return None, None
    if lags is None:
        lags = max(1, int(np.ceil(n ** (1.0 / 3.0))))
    lags = max(0, min(lags, n - 2))

    mu = float(s.mean())
    if abs(mu) < 1e-15:
        return 0.0, 1.0

    gamma = {}
    gamma[0] = float(np.mean((s - mu) ** 2))
    for lag in range(1, lags + 1):
        gamma[lag] = float(np.mean((s[:-lag] - mu) * (s[lag:] - mu)))

    # Bartlett 核加权
    var = gamma[0]
    for lag in range(1, lags + 1):
        w = 1.0 - lag / (lags + 1.0)
        var += 2.0 * w * gamma[lag]
    var = max(var, 1e-12)

    se = np.sqrt(var / n)
    t = mu / se
    p = 2.0 * stats.t.sf(abs(t), df=max(n - 1, 1))
    return float(t), float(p)


class StatisticalSignificance:
    """统计显著性检验：对 IC 序列做 t-test（Newey-West 校正）。"""

    @staticmethod
    def test(ic_series: pd.Series, alpha: float = 0.05, lags: int = None) -> dict:
        """对 IC 序列做 Newey-West 校正 t 检验。

        H0: mean IC = 0
        H1: mean IC != 0

        Args:
            ic_series: IC 序列
            alpha: 显著性水平（默认 0.05）
            lags: 自相关滞后阶数（None 自动，传入 horizon 更准确）

        Returns:
            {"t_stat": float, "p_value": float, "significant": bool, "n_days": int}
        """
        s = pd.Series(ic_series).dropna()
        n = len(s)
        if n < 3:
            return {"t_stat": None, "p_value": None, "significant": False, "n_days": n}

        t_stat, p_value = newey_west_t(s, lags=lags)
        if t_stat is None or np.isnan(t_stat) or np.isnan(p_value):
            return {"t_stat": None, "p_value": None, "significant": False, "n_days": n}

        return {
            "t_stat": round(t_stat, 4),
            "p_value": round(p_value, 4),
            "significant": bool(p_value < alpha),
            "n_days": n,
            "method": "newey-west",
            "lags": lags if lags is not None else max(1, int(np.ceil(n ** (1.0 / 3.0)))),
        }


# ==================== 因子多样性 ====================

class DiversityChecker:
    """因子多样性检测器。

    策略：
    1. 表达式标准化：常数折叠简化
    2. 相关性检测：与已有因子的 IC 时序相关性 > 0.8 视为冗余
    """

    @staticmethod
    def normalize(expr: str) -> str:
        """表达式标准化：去除多余空格、简化常数运算。"""
        # 去除多余空格
        expr = " ".join(expr.split())
        # 简化 * 1 和 / 1
        import re
        expr = re.sub(r'\* 1(\.0)?(?=\s|$)', '', expr)
        expr = re.sub(r'/ 1(\.0)?(?=\s|$)', '', expr)
        expr = re.sub(r'\* 1\.0(?=\s|$)', '', expr)
        expr = re.sub(r'/ 1\.0(?=\s|$)', '', expr)
        # 简化 + 0 和 - 0
        expr = re.sub(r'\+ 0(\.0)?(?=\s|$)', '', expr)
        expr = re.sub(r'- 0(\.0)?(?=\s|$)', '', expr)
        # 去除括号内的多余空白
        expr = expr.replace("( ", "(").replace(" )", ")")
        expr = expr.replace(", ", ",")
        return expr.strip()

    @staticmethod
    def is_duplicate_by_correlation(
        new_ic_series: pd.Series,
        existing_ic_series: list[pd.Series],
        threshold: float = 0.8,
    ) -> bool:
        """与已有因子的 IC 时序相关性去重。

        new_ic_series: 新因子的 IC 序列
        existing_ic_series: 已有因子的 IC 序列列表
        threshold: 相关阈值，超过该值视为重复

        Returns: True if 与任一已有因子高度相关
        """
        if len(new_ic_series) < 10 or not existing_ic_series:
            return False
        new_s = pd.Series(new_ic_series).dropna()
        for existing in existing_ic_series:
            old_s = pd.Series(existing).dropna()
            # 对齐索引
            aligned = pd.concat([new_s, old_s], axis=1).dropna()
            if len(aligned) < 10:
                continue
            corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
            if not np.isnan(corr) and abs(corr) > threshold:
                return True
        return False


def bh_corrected_pvalues(p_values: list) -> list:
    """Benjamini-Hochberg 多重检验校正（FDR 控制）。

    挖掘管线对一批候选因子做显著性筛选时，多次检验会放大假阳性率，
    用 BH 校正后的 q 值替代原始 p 值判断是否显著。

    Args:
        p_values: 原始 p 值列表（None 视为缺失，不参与校正，保持 None）

    Returns:
        与输入等长的校正后 q 值列表
    """
    valid_idx = [i for i, p in enumerate(p_values) if p is not None]
    if not valid_idx:
        return [None] * len(p_values)
    m = len(valid_idx)
    raw = np.asarray([p_values[i] for i in valid_idx], dtype=float)
    # 防御：p 值必须在 [0,1]
    raw = np.clip(raw, 0.0, 1.0)
    order = np.argsort(raw)
    ranks = np.arange(1, m + 1, dtype=float)
    q_sorted = raw[order] * m / ranks
    # 单调性约束：从后往前累积取 min
    q_sorted = np.minimum.accumulate(q_sorted[::-1])[::-1]
    q = np.empty(m)
    q[order] = q_sorted
    q = np.clip(q, 0.0, 1.0)
    out = [None] * len(p_values)
    for i, val in zip(valid_idx, q.tolist()):
        out[i] = float(val)
    return out


# ==================== 主验证器 ====================

# IC 缓存：key=md5(expr|start|end|horizon)，value=全量评价结果
_IC_CACHE: LRUCache = LRUCache(maxsize=1024)


def _ic_cache_key(expr: str, start: str, end: str, horizon: int, diversity: bool = False) -> str:
    """生成 IC 缓存 key（diversity 是否启用会影响结果，必须纳入 key）。"""
    import hashlib
    raw = f"{expr}|{start}|{end}|{horizon}|{'div' if diversity else 'nodiv'}"
    return hashlib.md5(raw.encode()).hexdigest()


def _ic_cache_put(key: str, value: dict) -> None:
    """写入 IC 缓存（LRUCache 自动淘汰最久未使用的条目）。"""
    _IC_CACHE[key] = value


def _ic_cache_get(key: str) -> dict | None:
    """读取 IC 缓存。"""
    return _IC_CACHE.get(key)


def get_ic_cache_size() -> int:
    """返回当前 IC 缓存大小（用于监控）。"""
    return len(_IC_CACHE)


def clear_ic_cache() -> None:
    """清空 IC 缓存（用于测试/重置）。"""
    _IC_CACHE.clear()


def compute_daily_ic_series(factor_expr: str, start: str, end: str,
                            universe: str = None, horizon: int = 5,
                            factor_df: pd.DataFrame = None,
                            label_df: pd.DataFrame = None) -> pd.Series:
    """计算每日截面 Spearman IC 序列。

    使用 horizon 周期前向收益作为标签。
    复用 factor_eval 中的数据加载逻辑。

    Args:
        factor_df/label_df: 已加载的因子/标签数据（避免重复加载，
            批量评价或分段计算时传入，start/end 将作为日期过滤范围）。

    Returns:
        pd.Series(index=datetime, values=daily_rank_ic)
    """
    from app.services.quant.factor_eval import load_factor_values, load_label
    label_expr = f"Ref($close, -{horizon}) / $close - 1"
    if factor_df is None:
        factor_df = load_factor_values(factor_expr, start, end, universe)
    if label_df is None:
        label_df = load_label(start, end, label_expr=label_expr, universe=universe)
    # 日期过滤（传入已加载数据且要求分段时生效）
    if factor_df is not None and start:
        dts = factor_df.index.get_level_values("datetime")
        factor_df = factor_df[(dts >= pd.Timestamp(start)) & (dts <= pd.Timestamp(end))]
    if label_df is not None and start:
        dts = label_df.index.get_level_values("datetime")
        label_df = label_df[(dts >= pd.Timestamp(start)) & (dts <= pd.Timestamp(end))]
    merged = factor_df.join(label_df, how="inner").dropna()
    if merged.empty:
        return pd.Series(dtype=float)

    daily_ic = merged.groupby(level="datetime").apply(
        lambda g: g["factor"].corr(g["label"], method="spearman")
        if len(g) >= 10 else np.nan,
        include_groups=False,
    ).dropna()
    return daily_ic


# 已有因子 IC 序列缓存（多样性检测用）：key=md5(expr|start|end|horizon)
_EXISTING_IC_CACHE: LRUCache = LRUCache(maxsize=256)


def _existing_ic_cache_key(expr: str, start: str, end: str, horizon: int) -> str:
    import hashlib
    raw = f"{expr}|{start}|{end}|{horizon}"
    return hashlib.md5(raw.encode()).hexdigest()


def compute_existing_ic_series(exprs: list, start: str, end: str,
                               universe: str = None, horizon: int = 5) -> list[pd.Series]:
    """批量计算已有因子的 IC 序列（带缓存），供多样性检测使用。

    单个因子失败不影响整体（记录日志跳过），缓存命中时直接复用，
    避免每次挖掘任务重复加载已有因子的全量数据。
    """
    series_list = []
    for expr in exprs:
        key = _existing_ic_cache_key(expr, start, end, horizon)
        cached = _EXISTING_IC_CACHE.get(key)
        if cached is not None:
            series_list.append(cached)
            continue
        try:
            s = compute_daily_ic_series(expr, start, end, universe, horizon)
        except Exception as e:
            logger.debug("已有因子 IC 序列计算失败 expr=%s: %s", expr, e)
            continue
        _EXISTING_IC_CACHE[key] = s
        series_list.append(s)
    return series_list


def evaluate_factor_with_validation(
    factor_expr: str,
    start: str,
    end: str,
    universe: str = None,
    horizon: int = 5,
    ic_threshold: float = None,
    significance_alpha: float = 0.05,
    stability_threshold: float = 0.5,
    positive_ratio_threshold: float = 0.55,
    decay_threshold: float = -0.01,
    existing_ic_series: list[pd.Series] = None,
    diversity_threshold: float = 0.8,
    baseline_exprs: list = None,
    roll_windows: list = None,
    industry_neutralize_enabled: bool = False,
) -> dict:
    """完整因子多维验证。

    流程：
    1. 样本分割：train/valid/test
    2. 计算各段 IC 序列
    3. 滚动 IC 统计量
    4. 统计显著性检验
    5. 多样性检测
    6. 综合评分与筛选

    Args:
        factor_expr: qlib 因子表达式
        start: 评价起始日期
        end: 评价结束日期
        universe: 股票池
        horizon: 预测周期（标签前向收益天数）
        ic_threshold: IC 阈值（默认从 config 读取）
        significance_alpha: 显著性水平
        stability_threshold: 稳定性阈值（IC 信息比率）
        positive_ratio_threshold: IC > 0 占比阈值
        decay_threshold: 衰减阈值（低于此值视为衰减严重）
        existing_ic_series: 已有因子的 IC 序列列表（用于多样性去重）
        diversity_threshold: 多样性相关阈值

    Returns:
        {
            "valid_ic": float,              # 验证集 IC（主筛选指标）
            "test_ic": float,               # 测试集 IC（仅记录，不参与筛选）
            "ic": float,                    # 全样本 IC（向后兼容）
            "rank_ic": float,               # 全样本 RankIC
            "icir": float,                  # 全样本 ICIR
            "valid_icir": float,            # 验证集 ICIR
            "stability": float,             # 稳定性
            "positive_ratio": float,        # IC > 0 占比
            "decay": float,                 # 衰减
            "significant": bool,            # 统计显著
            "is_duplicate": bool,           # 是否与已有因子重复
            "passed": bool,                 # 是否通过所有筛选
            "train_ic": float,              # 训练集 IC
            "valid_ic_series": list,        # 验证集 IC 序列
            "rolling_stats": dict,          # 滚动 IC 统计量
            "significance": dict,           # 显著性检验结果
            "sample_splits": dict,          # 样本分割信息
            # 以下字段向后兼容
            "turnover": float,
            "eval_start": start,
            "eval_end": end,
            "factor_expr": factor_expr,
            "horizon": horizon,
        }
    """
    mining_cfg = settings.mining.get("llm", {})
    if ic_threshold is None:
        ic_threshold = mining_cfg.get("ic_threshold", 0.03)

    # 检查缓存（diversity 状态纳入 key，避免缓存污染多样性检测结果）
    cache_key = _ic_cache_key(factor_expr, start, end, horizon,
                              diversity=bool(existing_ic_series))
    cached = _ic_cache_get(cache_key)
    if cached is not None:
        return cached

    # 1. 样本分割：基于实际交易日（因子数据真实存在的日期），而非自然日
    from app.services.quant.factor_eval import (
        load_factor_values, load_label, compute_ic, compute_turnover
    )
    label_expr = f"Ref($close, -{horizon}) / $close - 1"
    factor_df = load_factor_values(factor_expr, start, end, universe)
    label_df = load_label(start, end, label_expr=label_expr, universe=universe)
    actual_dates = sorted(factor_df.index.get_level_values("datetime").unique())
    splits = SampleSplitter().split_by_dates(actual_dates)

    # 行业中性化（可选）：消除行业暴露造成的假 IC
    if industry_neutralize_enabled:
        try:
            from app.services.factor.neutralize import industry_neutralize
            from app.services.data.industry_sync import load_industry_map
            ind_map = load_industry_map()
            if ind_map:
                factor_df = industry_neutralize(factor_df, industry_map=ind_map)
                logger.info("因子 %s 已做行业中性化", factor_expr)
        except Exception as e:
            logger.debug("行业中性化失败，跳过: %s", e)

    # 加载基准因子值（正交后挖掘用）：候选因子对基准残差化
    baseline_factor_dfs = None
    if baseline_exprs:
        try:
            baseline_factor_dfs = [
                load_factor_values(e, start, end, universe) for e in baseline_exprs
            ]
        except Exception as e:
            logger.debug("基准因子加载失败: %s", e)

    # 2. 全样本 IC 序列（用于向后兼容 compute_ic 的全部指标）
    ic_metrics = compute_ic(factor_df, label_df)
    turnover = compute_turnover(factor_df)

    # 3. 分段计算 IC 序列（复用已加载数据，不再重复 IO）
    segment_ics = {}
    for seg_name, seg_dates in splits.items():
        if not seg_dates:
            continue
        seg_start = str(seg_dates[0].date())
        seg_end = str(seg_dates[-1].date())
        try:
            seg_ic = compute_daily_ic_series(
                factor_expr, seg_start, seg_end, universe, horizon,
                factor_df=factor_df, label_df=label_df,
            )
            segment_ics[seg_name] = seg_ic
        except Exception as e:
            logger.debug("分段 %s IC 计算失败: %s", seg_name, e)
            segment_ics[seg_name] = pd.Series(dtype=float)

    # 4. 验证集 IC 统计（主筛选指标）
    valid_ic = segment_ics.get("valid", pd.Series(dtype=float))
    valid_ic_mean = float(valid_ic.mean()) if len(valid_ic) > 0 else None
    valid_ic_std = float(valid_ic.std()) if len(valid_ic) > 1 else None
    valid_icir = float(valid_ic_mean / valid_ic_std) if valid_ic_mean and valid_ic_std else None

    # 5. 测试集 IC（仅记录）
    test_ic = segment_ics.get("test", pd.Series(dtype=float))
    test_ic_mean = float(test_ic.mean()) if len(test_ic) > 0 else None

    # 6. 滚动 IC 统计
    rolling_eval = RollingICEvaluator.evaluate(valid_ic)

    # 6.5 滚动窗口重验：把全样本按窗口滑动的多个子段 IC，取中位数作为稳健性
    roll_ics = []
    if roll_windows:
        all_dates = sorted(factor_df.index.get_level_values("datetime").unique())
        for win in roll_windows:
            if len(all_dates) < win * 2:
                continue
            for i in range(0, len(all_dates) - win + 1, max(1, win // 2)):
                sub_dates = all_dates[i:i + win]
                if len(sub_dates) < max(10, win // 2):
                    continue
                try:
                    sub_start = str(sub_dates[0].date())
                    sub_end = str(sub_dates[-1].date())
                    sub_ic = compute_daily_ic_series(
                        factor_expr, sub_start, sub_end, universe, horizon,
                        factor_df=factor_df, label_df=label_df,
                    )
                    if len(sub_ic) > 0:
                        roll_ics.append(float(sub_ic.mean()))
                except Exception:
                    continue
    roll_ic_median = float(np.median(roll_ics)) if roll_ics else None

    # 7. 统计显著性（Newey-West 校正，lags=horizon 对齐标签重叠周期）
    significance = StatisticalSignificance.test(valid_ic, alpha=significance_alpha, lags=horizon)

    # 8. 多样性检测
    is_duplicate = False
    if existing_ic_series and valid_ic_mean is not None:
        is_duplicate = DiversityChecker.is_duplicate_by_correlation(
            valid_ic, existing_ic_series, threshold=diversity_threshold
        )

    # 9. 综合筛选
    passed = True
    fail_reasons = []

    if valid_ic_mean is None or abs(valid_ic_mean) < ic_threshold:
        passed = False
        fail_reasons.append(f"valid_ic={valid_ic_mean} < 阈值{ic_threshold}")

    if significance.get("significant") is False and valid_ic_mean is not None:
        # 只有有数据时才检查显著性
        if len(valid_ic) >= 3:
            passed = False
            fail_reasons.append(f"统计不显著(p={significance.get('p_value', 'N/A')})")

    stability = rolling_eval.get("stability", 0)
    if stability is not None and stability < stability_threshold and valid_ic_mean is not None:
        passed = False
        fail_reasons.append(f"稳定性={stability} < 阈值{stability_threshold}")

    positive_ratio = rolling_eval.get("positive_ratio", 0)
    if positive_ratio is not None and positive_ratio < positive_ratio_threshold and valid_ic_mean is not None:
        passed = False
        fail_reasons.append(f"正占比={positive_ratio} < 阈值{positive_ratio_threshold}")

    decay = rolling_eval.get("decay", 0)
    if decay is not None and decay < decay_threshold:
        passed = False
        fail_reasons.append(f"衰减={decay} < 阈值{decay_threshold}")

    if is_duplicate:
        passed = False
        fail_reasons.append("与已有因子高度相关")

    result = {
        # 主筛选指标
        "valid_ic": round(valid_ic_mean, 4) if valid_ic_mean is not None else None,
        "valid_icir": round(valid_icir, 4) if valid_icir is not None else None,
        "test_ic": round(test_ic_mean, 4) if test_ic_mean is not None else None,
        "passed": passed,
        "fail_reasons": fail_reasons,

        # 向后兼容指标
        "ic": ic_metrics.get("ic"),
        "rank_ic": ic_metrics.get("rank_ic"),
        "icir": ic_metrics.get("icir"),
        "ir": ic_metrics.get("ir"),
        "turnover": turnover,

        # 分段 IC
        "train_ic": round(float(segment_ics.get("train", pd.Series(dtype=float)).mean()), 4)
        if len(segment_ics.get("train", pd.Series(dtype=float))) > 0 else None,
        "valid_ic_series": [float(round(v, 4)) for v in valid_ic.values] if len(valid_ic) > 0 else [],

        # 滚动统计
        "rolling_stats": rolling_eval,
        "roll_ic_median": round(roll_ic_median, 4) if roll_ic_median is not None else None,
        "roll_ic_windows": len(roll_ics),

        # 显著性
        "significance": significance,
        "significant": significance.get("significant", False),

        # 多样性
        "is_duplicate": is_duplicate,

        # 样本分割信息
        "sample_splits": {
            "train": {"start": splits["train"][0], "end": splits["train"][-1],
                      "n_days": len(splits["train"])} if splits["train"] else None,
            "valid": {"start": splits["valid"][0], "end": splits["valid"][-1],
                      "n_days": len(splits["valid"])} if splits["valid"] else None,
            "test": {"start": splits["test"][0], "end": splits["test"][-1],
                     "n_days": len(splits["test"])} if splits["test"] else None,
        },

        # 元信息
        "eval_start": start,
        "eval_end": end,
        "factor_expr": factor_expr,
        "horizon": horizon,
    }

    # 正交后 IC：候选因子对基准因子残差化后的增量 alpha
    if baseline_factor_dfs:
        try:
            from app.services.quant.factor_eval import compute_orthogonal_ic
            ortho = compute_orthogonal_ic(factor_df, baseline_factor_dfs, label_df)
            result["orthogonal_ic"] = ortho.get("orthogonal_ic")
            result["orthogonal_rank_ic"] = ortho.get("orthogonal_rank_ic")
            result["orthogonal_r2"] = ortho.get("r2")
        except Exception as e:
            logger.debug("正交 IC 计算失败: %s", e)

    # 写入缓存
    _ic_cache_put(cache_key, result)

    return result
