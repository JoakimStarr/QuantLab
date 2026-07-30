"""qlib 回测封装：基于 top-k dropout 选股策略。

输入：预测打分 DataFrame（MultiIndex datetime/instrument，含 score 列）
输出：组合日收益、成本、基准、换手

滑点：可选 slippage_bps（基点），默认 0。买入按 (1+slippage) 成交，卖出按 (1-slippage)。
"""
import logging
import numpy as np
import pandas as pd
from app.services.quant.qlib_init import init_qlib
from app.core.config import settings

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
        weights: {factor_name: weight}，equal_weight 时忽略
        method: equal_weight / ic_weight
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

    if method == "ic_weight" and weights:
        w = np.array([abs(weights.get(n, 0)) for n in names])
        s = w.sum()
        if s == 0:
            w = np.ones(len(names)) / len(names)
        else:
            w = w / s
    else:
        w = np.ones(len(names)) / len(names)

    combined["score"] = combined[names].values @ w
    return combined[["score"]].dropna()


def _is_price_limited(code: str, daily_ret: float) -> bool:
    """判断当日是否涨跌停（无法成交）。主板±10%，创业板/科创板±20%。"""
    if daily_ret is None or np.isnan(daily_ret):
        return True  # 停牌/无数据视为不可交易
    code_lower = code.lower()
    limit = 0.195 if code_lower.startswith(("sz30", "sh68")) else 0.095
    return daily_ret >= limit or daily_ret <= -limit


def run_backtest(
    score_df: pd.DataFrame,
    start: str = None,
    end: str = None,
    topk: int = None,
    n_drop: int = None,
    benchmark: str = None,
    rebalance_freq: str = "day",
    portfolio_method: str = None,
) -> dict:
    """运行 top-k dropout 回测（自实现，仅用 qlib 加载价格数据）。

    改进：
    - 涨跌停过滤：涨停股不可买入，跌停股不可卖出
    - 停牌过滤：成交量为 0 或收益为 NaN 的股票排除
    - 调仓频率：day/week/month，非调仓日保持持仓
    - 交易成本按换手率分别计算买卖成本
    - 组合优化：portfolio_method=equal_weight（等权）或 cvxpy_optimize（CVXPy 优化）

    Args:
        score_df: MultiIndex (datetime, instrument) 含 'score' 列
        rebalance_freq: day（每日）/ week（每5交易日）/ month（月初）
        portfolio_method: equal_weight（默认）/ cvxpy_optimize
    Returns:
        {returns, benchmark, turnover, portfolios, start_date, end_date, ...}
    """
    init_qlib()
    from qlib.data import D

    period = settings.quant.get("default_backtest_period", {})
    start = start or period.get("start", "2020-01-01")
    end = end or period.get("end", "2024-12-31")
    topk = topk or settings.quant.get("topk", 50)
    n_drop = n_drop or settings.quant.get("n_drop", 5)
    benchmark = benchmark or settings.quant.get("benchmark", "SH000300")
    cost_buy = settings.quant.get("cost_buy", 0.0013)
    cost_sell = settings.quant.get("cost_sell", 0.0023)

    # 组合优化配置
    portfolio_cfg = settings.quant.get("portfolio_optimizer", {})
    if portfolio_method is None:
        portfolio_method = "cvxpy_optimize" if portfolio_cfg.get("enabled") else "equal_weight"

    # 行业映射（仅启用组合优化时懒加载，传入后行业暴露约束才生效）
    industry_map = None
    if portfolio_method == "cvxpy_optimize":
        try:
            from app.services.data.industry_sync import load_industry_map
            industry_map = load_industry_map() or None
            if not industry_map:
                logger.info("行业映射未同步，组合优化行业暴露约束将不生效")
        except Exception as e:
            logger.warning("加载行业映射失败: %s", e)

    # 按区间过滤打分
    mask = (score_df.index.get_level_values("datetime") >= pd.Timestamp(start)) & \
           (score_df.index.get_level_values("datetime") <= pd.Timestamp(end))
    score_df = score_df[mask].copy()
    if score_df.empty:
        raise ValueError("打分数据为空")

    logger.info("回测: %s~%s topk=%d n_drop=%d freq=%s benchmark=%s portfolio=%s",
                start, end, topk, n_drop, rebalance_freq, benchmark, portfolio_method)

    # 加载收盘价与成交量
    instruments = sorted(score_df.index.get_level_values("instrument").unique())
    raw = D.features(instruments, ["$close", "$volume"], start_time=start, end_time=end, freq="day")
    if raw is None or raw.empty:
        raise ValueError("无法加载价格数据")
    price_df = raw["$close"].unstack(level="instrument")
    vol_df = raw["$volume"].unstack(level="instrument")
    # t+1 日收益（t 日收盘选股，t+1 日持有收益）
    returns_df = price_df.pct_change().shift(-1)
    # 当日涨跌幅（用于涨跌停判断）
    daily_chg = price_df.pct_change()

    # 调仓日判断
    all_dates = sorted(price_df.index)
    if rebalance_freq == "week":
        rebalance_dates = set(all_dates[::5])
    elif rebalance_freq == "month":
        rebalance_dates = set(d for d in all_dates if d.is_month_start or d == all_dates[0])
    else:
        rebalance_dates = set(all_dates)

    # top-k dropout 选股
    score_dates = sorted(score_df.index.get_level_values("datetime").unique())
    portfolio_returns = []
    holdings = None
    holdings_weights = None  # None=等权，dict=加权
    turnover_list = []
    for date in all_dates:
        if date not in returns_df.index:
            continue
        # 非调仓日：保持持仓，仅计算收益
        if date not in rebalance_dates and holdings:
            day_ret = _calc_holding_return(returns_df, daily_chg, vol_df, date, holdings, holdings_weights)
            if day_ret is not None:
                portfolio_returns.append({"date": date, "return": day_ret})
            continue

        # 调仓日：重新选股
        try:
            day_scores = score_df.xs(date, level="datetime")["score"].dropna()
        except KeyError:
            day_scores = pd.Series(dtype=float)
        if len(day_scores) == 0:
            # 无打分数据，保持持仓
            if holdings:
                day_ret = _calc_holding_return(returns_df, daily_chg, vol_df, date, holdings, holdings_weights)
                if day_ret is not None:
                    portfolio_returns.append({"date": date, "return": day_ret})
            continue

        # 过滤停牌（当日无成交量）与涨停（不可买入）
        tradable = []
        for inst in day_scores.index:
            if inst not in returns_df.columns:
                continue
            vol = vol_df.loc[date, inst] if date in vol_df.index and inst in vol_df.columns else 0
            if vol is None or np.isnan(vol) or vol <= 0:
                continue  # 停牌
            chg = daily_chg.loc[date, inst] if date in daily_chg.index else 0
            if _is_price_limited(inst, chg if not np.isnan(chg) else None):
                continue  # 涨跌停不可买入
            tradable.append(inst)
        day_scores = day_scores.reindex(tradable)
        if len(day_scores) == 0:
            continue

        # dropout 选股
        if holdings and n_drop > 0:
            old_in_holdings = day_scores.reindex(list(holdings)).dropna()
            # 跌停股不可卖出，强制保留
            non_sellable = {s for s in old_in_holdings.index
                            if _is_price_limited(s, daily_chg.loc[date, s] if date in daily_chg.index and s in daily_chg.columns else None)}
            keep_size = max(0, topk - n_drop)
            keep = old_in_holdings.sort_values(ascending=False).iloc[:keep_size].index.tolist()
            keep = list(set(keep) | non_sellable)  # 跌停强制保留
            candidates = day_scores.drop(index=[s for s in keep if s in day_scores.index])
            new_picks = candidates.sort_values(ascending=False).iloc[:n_drop].index.tolist()
            selected = keep + new_picks
        else:
            selected = day_scores.sort_values(ascending=False).iloc[:min(topk, len(day_scores))].index.tolist()

        selected = [s for s in selected if s in returns_df.columns]
        if not selected:
            continue

        # 计算持仓权重：等权或 CVXPy 优化
        if portfolio_method == "cvxpy_optimize" and len(selected) > 1:
            try:
                from app.services.quant.portfolio_optimizer import optimize_portfolio
                day_scores_selected = day_scores.reindex(selected).dropna()
                if len(day_scores_selected) > 1:
                    opt_cfg = {
                        "method": portfolio_cfg.get("method", "mean_variance"),
                        "max_weight": portfolio_cfg.get("max_weight", 0.05),
                        "max_industry_exposure": portfolio_cfg.get("max_industry_exposure", 0.20),
                        "risk_aversion": portfolio_cfg.get("risk_aversion", 0.5),
                        "industry_map": industry_map,
                    }
                    w_series = optimize_portfolio(day_scores_selected, **opt_cfg)
                    holdings_weights = {k: v for k, v in w_series.items()
                                        if k in selected and v > 0}
                    if not holdings_weights:
                        holdings_weights = None
                else:
                    holdings_weights = None
            except Exception as e:
                logger.warning("CVXPy 优化失败，回退等权: %s", e)
                holdings_weights = None
        else:
            holdings_weights = None

        # 换手率（单边：新建仓股票占新持仓的比例）
        if holdings is not None:
            old_set, new_set = set(holdings), set(selected)
            denom = max(len(new_set), 1)
            turnover = len(new_set - old_set) / denom
            turnover_list.append(turnover)
        holdings = set(selected)

        # 组合 t+1 日收益（排除停牌/NaN），等权或加权
        day_ret = _calc_holding_return(returns_df, daily_chg, vol_df, date, holdings, holdings_weights)
        # 扣交易成本：单边换手率 × 买卖双边费率
        if turnover_list and day_ret is not None:
            day_ret = day_ret - turnover_list[-1] * (cost_buy + cost_sell)
        if day_ret is not None:
            portfolio_returns.append({"date": date, "return": day_ret})

    port_df = pd.DataFrame(portfolio_returns).set_index("date")["return"]
    port_df.name = "return"

    # 基准收益
    bench_ret = None
    try:
        bench_code = benchmark.lower()
        bench_price = D.features([bench_code], ["$close"], start_time=start, end_time=end, freq="day")
        if bench_price is not None and not bench_price.empty:
            bench_series = bench_price.unstack(level="instrument")["$close"].iloc[:, 0]
            bench_ret = bench_series.pct_change().shift(-1).dropna()
            bench_ret.name = "benchmark"
    except Exception as e:
        logger.warning("基准加载失败: %s", e)

    return {
        "returns": port_df.dropna(),
        "benchmark": bench_ret,
        "turnover": float(np.mean(turnover_list)) if turnover_list else None,
        "portfolios": portfolio_returns[:5],
        "start_date": start,
        "end_date": end,
        "topk": topk,
        "n_drop": n_drop,
        "rebalance_freq": rebalance_freq,
        "benchmark_code": benchmark,
        "portfolio_method": portfolio_method,
    }


def _calc_holding_return(returns_df, daily_chg, vol_df, date, holdings, weights=None) -> float:
    """计算持仓当日收益（排除停牌/NaN 股票）。

    Args:
        weights: None=等权，dict={stock: weight}=加权
    """
    if not holdings or date not in returns_df.index:
        return None
    rets = []
    wts = []
    for inst in holdings:
        if inst not in returns_df.columns:
            continue
        r = returns_df.loc[date, inst]
        if r is not None and not np.isnan(r):
            rets.append(r)
            if weights and inst in weights:
                wts.append(float(weights[inst]))
            else:
                wts.append(1.0)
    if not rets:
        return None
    if weights:
        total_w = sum(wts)
        if total_w > 0:
            return float(np.dot(rets, wts) / total_w)
    return float(np.mean(rets))
