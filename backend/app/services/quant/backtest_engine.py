"""qlib 回测封装：基于 top-k dropout 选股策略。

输入：预测打分 DataFrame（MultiIndex datetime/instrument，含 score 列）
输出：组合日收益、成本、基准、换手

滑点：可选 slippage_bps（基点），默认 0。买入按 (1+slippage) 成交，卖出按 (1-slippage)。
"""
import logging
import numpy as np
import pandas as pd
from app.core.config import settings
from app.services.quant.qlib_init import init_qlib

logger = logging.getLogger(__name__)


def combine_factors(
    factor_values: dict,
    weights: dict = None,
    method: str = "equal_weight",
    orthogonalize: bool = False,
) -> pd.DataFrame:
    """将多因子值组合为打分。

    Args:
        factor_values: {factor_name: MultiIndex DataFrame with 'factor' col}
        weights: {factor_name: weight}（带符号，如 IC/ICIR）。
            ic_weight/ir_weight 时权重按绝对值归一化并保留符号；
            equal_weight 时按权重符号翻转因子方向（负权重 = 反向因子），
            权重缺失或为 0 的因子按正向处理。
        method: equal_weight / ic_weight / ir_weight
    Returns:
        MultiIndex DataFrame with 'score' column
    """
    if not factor_values:
        raise ValueError("因子列表为空")
    names = list(factor_values.keys())

    # 可选：按 IC 绝对值降序做 Gram-Schmidt 截面正交化，降低共线性
    if orthogonalize and len(names) > 1:
        from app.services.factor.orthogonalize import gram_schmidt_orthogonalize
        if weights:
            ic_order = sorted(names, key=lambda n: abs(weights.get(n, 0)), reverse=True)
        else:
            ic_order = names
        factor_values = gram_schmidt_orthogonalize(factor_values, ic_order)

    # 对齐到公共索引
    dfs = []
    for name in names:
        s = factor_values[name]["factor"].rename(name)
        # 截面标准化（z-score）避免量纲影响；用 ddof=0 防止单元素组 std=NaN

        def _zscore(x):
            std = x.std(ddof=0)
            if std is None or std == 0 or np.isnan(std):
                return x * 0.0
            return (x - x.mean()) / std
        s = s.groupby(level="datetime").transform(_zscore)
        dfs.append(s)
    combined = pd.concat(dfs, axis=1)

    if method in ("ic_weight", "ir_weight") and weights:
        # 保留符号：负 IC 因子（反向因子）权重为负，避免方向反转
        w = np.array([weights.get(n, 0.0) or 0.0 for n in names])
        s = np.abs(w).sum()
        if s == 0:
            w = np.ones(len(names)) / len(names)
        else:
            w = w / s
    elif weights:
        # equal_weight 等权：按 IC 符号翻转方向（负 IC 因子取反向）
        w = np.array([1.0 if (weights.get(n, 0.0) or 0.0) >= 0 else -1.0 for n in names])
        s = np.abs(w).sum()
        if s == 0:
            w = np.ones(len(names)) / len(names)
        else:
            w = w / s
    else:
        w = np.ones(len(names)) / len(names)

    combined["score"] = combined[names].values @ w
    return combined[["score"]].dropna()


def compute_combine_weights(
    factor_exprs: dict,
    start: str,
    end: str,
    method: str = "ic_weight",
    window: int = 60,
    horizon: int = 5,
    universe: str = None,
) -> dict:
    """从历史滚动 IC/ICIR 自动计算因子组合权重。

    用于 ic_weight / ir_weight 加权模式，替代静态的因子库 IC 字段。
    调用方拿到权重后传入 combine_factors 的 weights 参数。

    Args:
        factor_exprs: {factor_name: expression}
        method: ic_weight（滚动 IC 均值）/ ir_weight（滚动 ICIR = IC均值/IC标准差）
        window: 取最近 N 个交易日的 IC 序列计算，避免全期静态值
        horizon: 标签周期（未来 N 日收益）
        universe: 股票池，None 用配置默认
    Returns:
        {factor_name: weight}，权重按绝对值归一化（和为 1，保留符号）
    """
    from app.services.quant.factor_eval import (
        load_factor_values, load_label, _daily_rank_ic_series, forward_return_label,
    )

    label_expr = forward_return_label(horizon)
    try:
        label_df = load_label(start, end, label_expr=label_expr, universe=universe)
    except Exception as e:
        logger.warning("加载标签失败，权重退化为等权: %s", e)
        n = len(factor_exprs)
        return {k: 1.0 / n for k in factor_exprs} if n else {}

    weights = {}
    for name, expr in factor_exprs.items():
        try:
            factor_df = load_factor_values(expr, start, end, universe=universe)
            daily_ic = _daily_rank_ic_series(factor_df, label_df)
            if daily_ic.empty:
                weights[name] = 0.0
                continue
            recent_ic = daily_ic.tail(window) if len(daily_ic) > window else daily_ic
            ic_mean = float(recent_ic.mean())
            if method == "ir_weight":
                ic_std = float(recent_ic.std())
                weights[name] = float(ic_mean / ic_std) if ic_std and not np.isnan(ic_std) else 0.0
            else:  # ic_weight
                weights[name] = ic_mean
        except Exception as e:
            logger.warning("计算因子 %s 组合权重失败: %s", name, e)
            weights[name] = 0.0

    # 归一化（按绝对值，保留符号；符号方向在 combine_factors 中由 abs() 处理）
    total = sum(abs(v) for v in weights.values())
    if total == 0:
        n = len(weights)
        return {k: 1.0 / n for k in weights} if n else {}
    return {k: v / total for k, v in weights.items()}


def clamp_backtest_end(end: str, score_df: pd.DataFrame = None) -> str:
    """将回测 end 收敛到安全上界，manager 预收敛与 run_backtest 兜底共用同一逻辑。

    两个上界（取更早者）：
    1. 交易日历倒数第二日：qlib 需要 end 之后一天的价格计算最后一期收益，
       end 落在数据末日会越界（index N out of bounds with size N）；且当日
       日K 要收盘后才入库，end=今天 时当日数据缺失。
    2. 因子数据倒数第二日（传入 score_df 时启用）：因子信号只覆盖到数据末日，
       end 超过它会进入无信号尾段；直接调用 run_backtest 的路径由此兜底。

    返回归一化的 "YYYY-MM-DD" 字符串。
    """
    if not end:
        return end
    end_ts = pd.Timestamp(end)
    # 上界 1：交易日历
    try:
        from app.services.data.eod_incremental import _get_calendar
        data_cal = _get_calendar(settings.qlib_provider_path)
        if len(data_cal) >= 2:
            cal_end = pd.Timestamp(data_cal[-2])
            if end_ts > cal_end:
                logger.info("回测 end %s 收敛到数据倒数第二日 %s", end, data_cal[-2])
                end_ts = cal_end
    except Exception as e:  # noqa: BLE001
        logger.debug("回测 end 日历收敛失败（保持原值）: %s", e)
    # 上界 2：因子信号覆盖
    if score_df is not None and not score_df.empty:
        try:
            dates = sorted(score_df.index.get_level_values("datetime").unique())
            if len(dates) >= 2:
                score_end = pd.Timestamp(dates[-2].date())
                if end_ts > score_end:
                    logger.info("回测 end %s 收敛到因子数据倒数第二日 %s", end, score_end.date())
                    end_ts = score_end
        except Exception as e:  # noqa: BLE001
            logger.debug("回测 end 因子收敛失败（保持原值）: %s", e)
    return str(end_ts.date())


def run_backtest(
    score_df: pd.DataFrame,
    start: str = None,
    end: str = None,
    topk: int = None,
    n_drop: int = None,
    benchmark: str = None,
    rebalance_freq: str = "day",
    portfolio_method: str = None,
    backend: str = "qlib",
    capital: float = None,
    trade_unit: int = None,
    deal_price: str = None,
    slippage_bps: float = None,
    cost_buy: float = None,
    cost_sell: float = None,
    min_cost: float = None,
    asset_class: str = "stock",
) -> dict:
    """运行 top-k dropout 回测。

    backend:
        - "qlib"（默认）: QLib backtest_daily + TopkDropoutStrategy（工业级，原生A股约束）
        - "vbt": VectorBT 矢量化回测（高频调仓 A/B，快但无 strict A 股执行约束）

    执行/成本参数（用户可选，透传 qlib exchange）：
        trade_unit / deal_price / slippage_bps / cost_buy / cost_sell / min_cost
        vbt 后端支持 slippage_bps 与 cost_buy/cost_sell（fees）；trade_unit 在 vbt 无原生整手取整。

    asset_class:
        - "stock"（默认）: A 股约束（T+1、整手、涨跌停）
        - "etf": ETF 约束（T+0 语义、无整手 trade_unit=1、涨跌停放宽）

    收敛说明：自研 "self" 逐日回测已被 qlib（工业级约束）覆盖，已移除。
    vbt 与 qlib 功能重叠部分收敛为：严格约束/生产用 qlib，快速扫描 A/B 用 vbt。

    Args:
        score_df: MultiIndex (datetime, instrument) 含 'score' 列
        rebalance_freq: day（每日）/ week（每5交易日）/ month（月初）
        portfolio_method: equal_weight（默认）/ cvxpy_optimize
    Returns:
        {returns, benchmark, turnover, portfolios, start_date, end_date, ...}
    """
    # 回测区间不超过数据实际范围：调用方（前端/参数扫描）可能传 end=今天，
    # 而当日数据未入库；且 qlib 需要 end 之后一天的数据算最后一期收益，
    # end 落在数据末日会越界（index N out of bounds with size N）。
    # qlib/vbt 统一在此收敛（含 score_df 因子覆盖上界），避免各后端口径漂移。
    end = clamp_backtest_end(end, score_df)

    # backend=qlib（默认，工业级 A 股约束）：任何非 vbt 值都归一为 qlib
    if backend == "vbt":
        from app.services.quant.vbt_backtest import run_vbt_backtest
        return run_vbt_backtest(
            score_df, start=start, end=end, topk=topk, n_drop=n_drop,
            benchmark=benchmark, rebalance_freq=rebalance_freq,
            portfolio_method=portfolio_method, capital=capital,
            slippage_bps=slippage_bps, cost_buy=cost_buy, cost_sell=cost_sell,
            asset_class=asset_class,
        )

    init_qlib()
    from app.services.quant.qlib_backtest import run_qlib_backtest
    return run_qlib_backtest(
        score_df, start=start, end=end, topk=topk, n_drop=n_drop,
        benchmark=benchmark, rebalance_freq=rebalance_freq,
        portfolio_method=portfolio_method, capital=capital,
        trade_unit=trade_unit, deal_price=deal_price,
        slippage_bps=slippage_bps, cost_buy=cost_buy, cost_sell=cost_sell,
        min_cost=min_cost, asset_class=asset_class,
    )

