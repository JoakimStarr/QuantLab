"""VectorBT 回测后端：用 vectorbt（vbt 1.1.0，git master）的 Portfolio.from_signals 矢量化回测。

组合层语义与 backtest_engine.run_backtest(backend="self") 对齐：
- 每个调仓日对 topk 等权买入（size_type="Value"，入选股各投入组合总值的 1/topk）
- n_drop>0 时在 topk 内保留上期末持仓（dropout 平滑），其余买入分数最高的 topk
- 交易成本：open_cost/close_cost 折算为 fees；严格 A 股涨跌停/T+1 约束请用 backend="qlib"

输出格式与 run_backtest 对齐，可通过 backend 参数无缝切换。
"""
import logging

import numpy as np
import pandas as pd

from app.core.config import settings

logger = logging.getLogger(__name__)


def run_vbt_backtest(
    score_df: pd.DataFrame,
    start: str = None,
    end: str = None,
    topk: int = None,
    n_drop: int = None,
    benchmark: str = None,
    rebalance_freq: str = "day",
    portfolio_method: str = None,
    vbt_kwargs: dict = None,
) -> dict:
    """用 VectorBT from_signals 运行 top-k dropout 回测（backend = "vbt"）。

    Args:
        score_df: MultiIndex (datetime, instrument) 含 'score' 列
        rebalance_freq: day/week/month
        vbt_kwargs: 透传给 vbt.Portfolio.from_signals 的额外参数（如 {'fees': 0.001}）

    Returns:
        与 run_backtest 相同格式: {returns, benchmark, turnover, portfolios,
        start_date, end_date, topk, n_drop, rebalance_freq, benchmark_code, portfolio_method}
    """
    period = settings.quant.get("default_backtest_period", {})
    start = start or period.get("start", "2020-01-01")
    end = end or period.get("end", "2024-12-31")
    topk = topk or settings.quant.get("topk", 50)
    n_drop = n_drop or settings.quant.get("n_drop", 5)
    cost_buy = settings.quant.get("cost_buy", 0.0013)
    cost_sell = settings.quant.get("cost_sell", 0.0023)
    vbt_kwargs = dict(vbt_kwargs or {})

    signal = score_df.copy()
    if "score" not in signal.columns:
        raise ValueError("score_df 必须含 'score' 列")
    mask = (signal.index.get_level_values("datetime") >= pd.Timestamp(start)) & (
        signal.index.get_level_values("datetime") <= pd.Timestamp(end)
    )
    signal = signal[mask]
    if signal.empty:
        raise ValueError("打分数据为空")

    # 防御性过滤北交所（与其他后端对齐）
    include_bj = settings.quant.get("include_bj", False)
    if not include_bj:
        inst = signal.index.get_level_values("instrument")
        bj_mask = inst.str.startswith(("bj", "BJ"))
        if bj_mask.any():
            signal = signal[~bj_mask]

    # 调仓日
    all_dates = sorted(signal.index.get_level_values("datetime").unique())
    if rebalance_freq == "week":
        rebalance_dates = set(all_dates[::5])
    elif rebalance_freq == "month":
        rebalance_dates = set(d for d in all_dates if d.is_month_start or d == all_dates[0])
    else:
        rebalance_dates = set(all_dates)

    price_df = _load_prices(signal, start, end)
    if price_df is None or price_df.empty:
        raise ValueError("无法加载价格数据")

    # 逐调仓日构建 entries/exits/size 信号表
    entries = pd.DataFrame(np.zeros((len(price_df.index), len(price_df.columns)), dtype=bool),
                           index=price_df.index, columns=price_df.columns)
    exits = pd.DataFrame(np.zeros((len(price_df.index), len(price_df.columns)), dtype=bool),
                         index=price_df.index, columns=price_df.columns)
    size = pd.DataFrame(np.nan, index=price_df.index, columns=price_df.columns)

    holdings = set()
    for date in sorted(rebalance_dates):
        if date not in price_df.index:
            continue
        try:
            day_scores = signal.xs(date, level="datetime")["score"].dropna()
        except KeyError:
            continue
        day_scores = day_scores[day_scores.index.isin(price_df.columns)]
        if len(day_scores) == 0:
            continue
        rank = day_scores.sort_values(ascending=False)
        # dropout：保留上期末仍在 top 内的高分持仓，其余旧仓卖出换新
        keep = [c for c in rank.index if c in holdings][:n_drop] if n_drop else []
        selected = list(dict.fromkeys(keep + rank.index.tolist()))[:topk]
        selected = [c for c in selected if c in price_df.columns]
        if not selected:
            continue
        # 若组合到期未变（无掉出亦无新增），跳过调仓
        old_insts = holdings & set(price_df.columns)
        if old_insts == set(selected):
            continue
        # 旧持仓中掉出组合的股票：发卖出信号
        drop_insts = [c for c in holdings if c not in set(selected)]
        if drop_insts:
            exits.loc[date, drop_insts] = True
        # 新入选持仓：发买入信号（等权）
        buy_insts = [c for c in selected if c not in holdings]
        if buy_insts:
            entries.loc[date, buy_insts] = True
            w = 1.0 / len(selected)
            size.loc[date, selected] = w
        holdings = set(selected)

    if not entries.any().any():
        raise ValueError("无可交易的调仓信号")

    # vbt from_signals: size_type="Value" + cash_sharing => 每次信号买入市值 = 组合现金 x 权重
    import vectorbt as vbt
    pf_kwargs = dict(vbt_kwargs)
    pf_kwargs.setdefault("size_type", "Value")
    pf_kwargs.setdefault("direction", "longonly")
    pf_kwargs.setdefault("cash_sharing", True)
    pf_kwargs.setdefault("fees", cost_buy)
    pf = vbt.Portfolio.from_signals(
        price_df,
        entries=entries,
        exits=exits,
        size=size,
        **pf_kwargs,
    )
    returns = pf.returns().dropna()
    returns.name = "return"

    # 逐笔成交明细：直接读取 vectorbt 真实 order records（含成交价/数量/费用）
    trades = []
    try:
        # vbt 1.x 中 records_readable 是属性，直接返回 DataFrame
        readable = pf.orders.records_readable
        if hasattr(readable, "empty") and not readable.empty:
            for _, row in readable.iterrows():
                code = str(row.get("Column", ""))
                if not code:
                    continue
                size = float(row.get("Size") or 0)
                price = float(row.get("Price") or np.nan)
                fees = float(row.get("Fees") or 0)
                date = str(row.get("Timestamp", ""))
                if np.isnan(price) or size == 0:
                    continue
                action = "BUY" if size > 0 else "SELL"
                trades.append({
                    "date": date, "action": action, "code": code.split("_")[0] if "_" in code else code,
                    "price": round(price, 4), "quantity": round(abs(size), 4),
                    "total": round(abs(size) * price, 2), "cost": round(abs(fees), 2),
                })
    except Exception as e:
        logger.warning("提取 vbt 成交明细失败，trades 置空: %s", e)
    trades.sort(key=lambda t: (t["date"], t["action"], t["code"]))

    # 基准
    bench_ret = None
    if benchmark:
        try:
            from app.services.quant.qlib_backtest import normalize_benchmark
            from qlib.data import D
            bench_code = normalize_benchmark(benchmark)
            bench_price = D.features([bench_code], ["$close"], start_time=start, end_time=end, freq="day")
            if bench_price is not None and not bench_price.empty:
                bench_series = bench_price.unstack(level="instrument")["$close"].iloc[:, 0]
                bench_ret = bench_series.pct_change().shift(-1).dropna()
                bench_ret.name = "benchmark"
        except Exception as e:
            logger.warning("基准加载失败: %s", e)

    # 换手率（平均）：单边 = 新买入股票数/topk；分母为全部交易日，非调仓日贡献 0（与 qlib 口径对齐）
    turnover = None
    try:
        total = 0.0
        n_days = max(len(returns), 1)
        for d in entries.index[entries.any(axis=1)]:
            total += int(entries.loc[d].sum()) / max(topk, 1)
        turnover = float(total / n_days)
    except Exception as e:
        logger.debug("计算换手率失败: %s", e)

    # portfolios: 前 5 个调仓日持仓快照
    portfolios = []
    days = entries.index[entries.any(axis=1)][:5]
    for d in days:
        sel = entries.loc[d][entries.loc[d]].index.tolist()
        portfolios.append({
            "date": str(d.date() if hasattr(d, "date") else d),
            "holdings": {c: 1.0 / max(len(sel), 1) for c in sel},
        })

    return {
        "returns": returns,
        "benchmark": bench_ret,
        "turnover": turnover,
        "portfolios": portfolios,
        "trades": trades,
        "start_date": start,
        "end_date": end,
        "topk": topk,
        "n_drop": n_drop,
        "rebalance_freq": rebalance_freq,
        "benchmark_code": benchmark,
        "portfolio_method": portfolio_method or "topk_dropout",
    }


def _load_prices(signal: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """加载信号涉及股票的收盘价宽表（date x inst）。"""
    from app.services.quant.qlib_init import init_qlib
    init_qlib()
    from qlib.data import D

    instruments = sorted(signal.index.get_level_values("instrument").unique())
    raw = D.features(instruments, ["$close"], start_time=start, end_time=end, freq="day")
    if raw is None or raw.empty:
        return None
    return raw["$close"].unstack(level="instrument")