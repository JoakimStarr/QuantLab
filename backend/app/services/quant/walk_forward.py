"""Walk-forward 滚动回测。

将样本期切成多个 [训练, 测试] 滚动窗口：
- 训练窗：遍历候选 topk，选夏普最高的 topk 作为本窗最优参数
- 测试窗：用最优参数做样本外回测
- 拼接所有测试窗收益，评估整体样本外绩效与跨窗一致性

注意：run_backtest 内部自行通过 qlib 加载价格并使用 settings.quant 的成本参数，
因此本模块不直接消费 price_df / cost_*（保留参数仅为 API 兼容）。
"""
import logging
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TRADING_DAYS = 252


def run_walk_forward(
    score_df: pd.DataFrame,
    price_df: pd.DataFrame = None,   # 兼容保留，run_backtest 自行加载价格
    train_window: str = "730D",      # 训练窗口（约2年）
    test_window: str = "180D",      # 测试窗口（约6个月）
    step: str = "180D",             # 滚动步长
    topk_candidates: list = None,
    n_drop: int = 5,
    rebalance: str = "day",
    cost_buy: float = 0.0013,       # 兼容保留，run_backtest 用 settings.quant.cost_buy
    cost_sell: float = 0.0023,      # 兼容保留，run_backtest 用 settings.quant.cost_sell
    benchmark: str = None,
) -> dict:
    """Walk-forward 滚动回测

    Args:
        score_df: 因子打分，index=(datetime, instrument), columns 含 "score"
        price_df: 价格数据（保留参数，run_backtest 内部自行加载）
        train_window: 训练期长度
        test_window: 测试期长度
        step: 滚动步长
        topk_candidates: 候选 topk 值列表
        n_drop: 每期剔除数
        rebalance: 调仓频率
        cost_buy/cost_sell: 保留兼容（实际成本由 run_backtest 从 settings.quant 读取）
        benchmark: 基准代码

    Returns:
        {
            "windows": [window_results],
            "n_windows": int,
            "oos_returns": [daily_returns],
            "oos_nav": [nav_curve],
            "oos_metrics": {sharpe, max_dd, annual_return, ...},
            "consistency": {sharpe_std, sharpe_mean, ...},
            "best_params_per_window": [{window, topk, sharpe}],
        }
    """
    from app.services.quant.backtest_engine import run_backtest
    from app.services.quant.portfolio import analyze_portfolio

    if topk_candidates is None:
        topk_candidates = [10, 20, 30, 50]

    if score_df is None or score_df.empty:
        return {"error": "打分数据为空"}

    all_dates = sorted(score_df.index.get_level_values("datetime").unique())
    if len(all_dates) < 10:
        return {"error": "数据不足以进行 walk-forward 回测"}

    start = pd.Timestamp(all_dates[0])
    end = pd.Timestamp(all_dates[-1])

    train_delta = pd.Timedelta(train_window)
    test_delta = pd.Timedelta(test_window)
    step_delta = pd.Timedelta(step)

    windows = []
    oos_returns_all = []
    best_params = []

    train_start = start
    window_idx = 0

    while train_start + train_delta + test_delta <= end:
        train_end = train_start + train_delta
        test_start = train_end
        test_end = test_start + test_delta

        # 训练期：遍历 topk 找最优参数
        best_topk = topk_candidates[0]
        best_train_sharpe = -999.0
        for topk in topk_candidates:
            try:
                train_bt = run_backtest(
                    score_df,
                    start=str(train_start.date()), end=str(train_end.date()),
                    topk=topk, n_drop=n_drop,
                    rebalance_freq=rebalance,
                    benchmark=benchmark,
                )
                train_returns = train_bt.get("returns")
                if train_returns is None or len(train_returns) < 2:
                    continue
                train_metrics = analyze_portfolio(train_returns)
                train_sharpe = train_metrics.get("sharpe")
                if train_sharpe is None:
                    continue
                if train_sharpe > best_train_sharpe:
                    best_train_sharpe = train_sharpe
                    best_topk = topk
            except Exception as e:
                logger.warning("训练期回测失败 topk=%d: %s", topk, e)

        # 测试期：用最优参数回测
        try:
            test_bt = run_backtest(
                score_df,
                start=str(test_start.date()), end=str(test_end.date()),
                topk=best_topk, n_drop=n_drop,
                rebalance_freq=rebalance,
                benchmark=benchmark,
            )
            test_returns = test_bt.get("returns")
            if test_returns is None or len(test_returns) == 0:
                logger.warning("测试期无收益数据 window=%d", window_idx)
                train_start += step_delta
                window_idx += 1
                continue

            test_metrics = analyze_portfolio(test_returns)
            test_ret_list = [float(r) for r in test_returns.tolist()]
            nav_series = (1 + test_returns).cumprod()
            test_nav = [round(float(v), 4) for v in nav_series.tolist()]

            window_result = {
                "window_idx": window_idx,
                "train_start": str(train_start.date()),
                "train_end": str(train_end.date()),
                "test_start": str(test_start.date()),
                "test_end": str(test_end.date()),
                "best_topk": best_topk,
                "train_sharpe": round(float(best_train_sharpe), 4),
                "test_sharpe": test_metrics.get("sharpe"),
                "test_annual_return": test_metrics.get("annual_return"),
                "test_max_dd": test_metrics.get("max_drawdown"),
                "test_returns": test_ret_list,
                "test_nav": test_nav,
            }

            windows.append(window_result)
            oos_returns_all.extend(test_ret_list)
            best_params.append({
                "window": window_idx,
                "topk": best_topk,
                "sharpe": test_metrics.get("sharpe"),
            })

            logger.info("Window %d: train=%s~%s, test=%s~%s, topk=%d, test_sharpe=%s",
                        window_idx, train_start.date(), train_end.date(),
                        test_start.date(), test_end.date(), best_topk,
                        test_metrics.get("sharpe"))
        except Exception as e:
            logger.error("测试期回测失败 window %d: %s", window_idx, e)

        train_start += step_delta
        window_idx += 1

    # 计算样本外整体指标
    oos_nav = []
    oos_metrics = {}
    consistency = {}
    if oos_returns_all:
        oos_returns = np.array(oos_returns_all)
        oos_nav_arr = np.cumprod(1 + oos_returns)
        oos_nav = oos_nav_arr.tolist()
        std_ret = float(np.std(oos_returns))
        oos_metrics = {
            "total_return": float(oos_nav_arr[-1] - 1),
            "annual_return": float(np.mean(oos_returns) * TRADING_DAYS),
            "annual_volatility": float(std_ret * np.sqrt(TRADING_DAYS)),
            "sharpe": float(np.mean(oos_returns) / std_ret * np.sqrt(TRADING_DAYS)) if std_ret > 0 else 0.0,
            "max_drawdown": float(np.min(oos_nav_arr / np.maximum.accumulate(oos_nav_arr) - 1)),
            "n_days": int(len(oos_returns)),
        }

        window_sharpes = [w["test_sharpe"] for w in windows if w.get("test_sharpe") is not None]
        if window_sharpes:
            consistency = {
                "sharpe_mean": float(np.mean(window_sharpes)),
                "sharpe_std": float(np.std(window_sharpes)),
                "sharpe_min": float(np.min(window_sharpes)),
                "sharpe_max": float(np.max(window_sharpes)),
                "positive_ratio": float(sum(1 for s in window_sharpes if s > 0) / len(window_sharpes)),
            }
        else:
            consistency = {"sharpe_mean": 0, "sharpe_std": 0, "sharpe_min": 0,
                           "sharpe_max": 0, "positive_ratio": 0}

    return {
        "windows": windows,
        "n_windows": len(windows),
        "oos_returns": oos_returns_all,
        "oos_nav": oos_nav,
        "oos_metrics": oos_metrics,
        "consistency": consistency,
        "best_params_per_window": best_params,
    }


def build_score_df_from_exprs(factor_exprs: dict, weights: dict,
                              method: str, start: str, end: str,
                              universe: str = None) -> pd.DataFrame:
    """由因子表达式构建组合打分 DataFrame（同步，需在线程池调用）。

    复用 _compute_backtest_sync 的组合逻辑，仅返回 score_df。
    universe: 标的池（None=config 默认）。
    """
    from app.services.quant.qlib_init import init_qlib
    from app.services.quant.factor_eval import load_factor_values
    from app.services.quant.backtest_engine import combine_factors

    init_qlib()
    factor_values = {}
    for name, expr in factor_exprs.items():
        factor_values[name] = load_factor_values(expr, start, end, universe=universe)
    score_df = combine_factors(factor_values, weights=weights, method=method)
    return score_df
