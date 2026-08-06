"""VectorBT 回测后端：用 vectorbt（vbt 1.1.0，git master）的 Portfolio.from_signals 矢量化回测。

组合层语义与 backtest_engine.run_backtest(backend="vbt") 对齐：
- 每个调仓日对 topk 等权买入（size_type="Value"，入选股各买入初始资金的 1/topk）
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
    capital: float = None,
    slippage_bps: float = None,
    cost_buy: float = None,
    cost_sell: float = None,
    asset_class: str = "stock",
) -> dict:
    """用 VectorBT from_signals 运行 top-k dropout 回测（backend = "vbt"）。

    执行/成本参数（用户可选）：
    - slippage_bps: 滑点（基点），写入 vbt slippage
    - cost_buy/cost_sell: 费率（vbt 为单费率，取 cost_buy 统一应用）
    注意：vbt 无原生 A股整手取整，trade_unit 不适用；严格约束请用 backend="qlib"。

    asset_class:
        - "stock"（默认）: T+1 执行（T-1 信号 → T 成交，与 qlib 后端口径一致）
        - "etf": T+0 语义（信号日收盘成交）。日频 bar 无法建模盘内买卖，
          这里用"当日信号当日收盘成交"近似（含轻微前视），快速 A/B 用；
          严格口径请用 qlib 后端（T+1 时序 + 无整手 + 涨跌停放宽）。

    Args:
        score_df: MultiIndex (datetime, instrument) 含 'score' 列
        rebalance_freq: day/week/month
        vbt_kwargs: 透传给 vbt.Portfolio.from_signals 的额外参数（如 {'fees': 0.001}）
        capital: 初始资金，作为 vbt init_cash；每个入选股以 capital/topk 金额买入（等权）

    Returns:
        与 run_backtest 相同格式: {returns, benchmark, turnover, portfolios,
        start_date, end_date, topk, n_drop, rebalance_freq, benchmark_code, portfolio_method}
    """
    is_etf = asset_class == "etf"
    period = settings.quant.get("default_backtest_period", {})
    start = start or period.get("start", "2020-01-01")
    end = end or period.get("end", "2024-12-31")
    topk = topk if topk is not None else settings.quant.get("topk", 50)
    n_drop = n_drop if n_drop is not None else settings.quant.get("n_drop", 5)
    cost_buy = cost_buy if cost_buy is not None else settings.quant.get("cost_buy", 0.0013)
    cost_sell = cost_sell if cost_sell is not None else settings.quant.get("cost_sell", 0.0023)
    slippage_bps = slippage_bps if slippage_bps is not None else settings.quant.get("slippage_bps", 0)
    init_cash = capital or settings.quant.get("initial_capital", 100000000)
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
        if is_etf:
            # ETF T+0 语义：信号日收盘成交（日频近似，含轻微前视，供快速 A/B）
            signal_date = date
        else:
            # T+1 执行：用 T-1 日收盘信号决定 T 日成交（与 qlib 后端口径一致）。
            # 否则 vbt 默认在同 bar 收盘价成交，而信号也用当日收盘价计算 → 同收盘价前视。
            pos = price_df.index.get_loc(date)
            if pos == 0:
                # 首个交易日没有前一日信号，无法交易
                continue
            signal_date = price_df.index[pos - 1]
        try:
            day_scores = signal.xs(signal_date, level="datetime")["score"].dropna()
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
            # size_type="Value" 是绝对金额（现金单位），不是权重。
            # 传入 capital/topk 使每个入选股买入等额（初始资金等权），
            # 否则把 1/topk 权重当金额会导致组合几乎空仓、收益趋近 0。
            size.loc[date, buy_insts] = init_cash / topk
        holdings = set(selected)

    if not entries.any().any():
        raise ValueError("无可交易的调仓信号")

    # vbt from_signals: size_type="Value" + cash_sharing => 每次信号买入 size 金额（绝对量）
    import vectorbt as vbt
    pf_kwargs = dict(vbt_kwargs)
    pf_kwargs.setdefault("init_cash", init_cash)
    pf_kwargs.setdefault("size_type", "Value")
    pf_kwargs.setdefault("direction", "longonly")
    pf_kwargs.setdefault("cash_sharing", True)
    pf_kwargs.setdefault("fees", cost_buy)
    if slippage_bps > 0:
        pf_kwargs.setdefault("slippage", slippage_bps / 10000.0)
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
                # 与策略 returns 同为"当日已实现收益"（按收盘价对齐），不 shift。
                # 旧代码 shift(-1) 会把基准提前一天，导致与策略收益错位、对比失真。
                bench_ret = bench_series.pct_change().dropna()
                bench_ret.name = "benchmark"
        except Exception as e:
            logger.warning("基准加载失败: %s", e)

    # 换手率（与 qlib report["turnover"] 口径对齐）：
    # turnover_t = 当日成交额（买+卖绝对值之和） / 前一日组合市值，再对全部交易日求平均。
    # 口径依据：qlib account.update_portfolio_metrics 中 turnover_rate = now_turnover / last_account_value，
    # 其中 now_turnover 累计每笔订单 |deal_amount * trade_price|（买卖同号取正，非净额）。
    turnover = None
    try:
        records = pf.orders.records_readable
        if hasattr(records, "empty") and not records.empty:
            # 按交易日汇总成交额（买+卖绝对值）
            gross = {}
            for _, row in records.iterrows():
                ts = pd.Timestamp(row.get("Timestamp"))
                val = abs(float(row.get("Size") or 0)) * float(row.get("Price") or np.nan)
                if not np.isnan(val):
                    gross[ts] = gross.get(ts, 0.0) + val
            pv = pf.value()
            n_days = max(len(pv), 1)
            total = 0.0
            prev_val = float(capital) if capital else (float(pv.iloc[0]) if len(pv) else 0.0)
            for i, d in enumerate(pv.index):
                v = gross.get(d, 0.0)
                if v > 0 and prev_val and prev_val > 0 and not np.isnan(prev_val):
                    total += v / prev_val
                prev_val = float(pv.iloc[i])
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