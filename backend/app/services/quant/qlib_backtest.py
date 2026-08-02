"""QLib 回测后端：用 QLib backtest_daily + TopkDropoutStrategy 实现工业级回测。

吸收开源项目（Kronos/qlib_test.py、QLib 官方 examples）的标准用法：
- TopkDropoutStrategy: top-k dropout 选股
- Exchange(SimulatorExecutor): A股交易约束（涨跌停/T+1/成本）原生支持
- risk_analysis: 绩效分析

输出格式与 backtest_engine.run_backtest 对齐，可通过 backend 参数无缝切换。

参考:
- https://qlib.readthedocs.io/en/latest/component/strategy.html
- https://deepwiki.com/shiyu-coder/Kronos/4.9-backtesting-with-qlib
"""
import logging
import pandas as pd
from app.services.quant.qlib_init import init_qlib
from app.core.config import settings

logger = logging.getLogger(__name__)


def _position_dates_mapping(positions_normal) -> list:
    """返回按日期排序的 (date, Position) 对；兼容 dict / Series / DataFrame。"""
    if isinstance(positions_normal, pd.DataFrame):
        entries = [(idx, row["position"]) for idx, row in positions_normal.iterrows()]
    elif isinstance(positions_normal, pd.Series):
        entries = list(positions_normal.items())
    elif isinstance(positions_normal, dict):
        entries = list(positions_normal.items())
    else:
        return []
    return [(k, v) for k, v in entries if v is not None]


def extract_trades_from_positions(positions_normal, topk: int = None) -> list:
    """从逐日持仓快照差分还原逐笔成交明细。

    QLib 不直接导出 order 明细，从相邻持仓快照 diff 得到：
    - 数量增加 → BUY（含新进建仓）
    - 数量减少或清仓 → SELL
    成交价近似为当日该持仓市价（Position.price 每日随行情更新，即 T+1 收盘成交价）。

    Returns:
        sorted trades: [{date, action, code, price, quantity, total, cost}]
    """
    positions = _position_dates_mapping(positions_normal)
    cfg = settings.quant
    cost_buy = cfg.get("cost_buy", 0.0013)
    cost_sell = cfg.get("cost_sell", 0.0023)

    trades = []
    prev_amounts: dict = {}
    prev_prices: dict = {}
    for date_key, pos in positions:
        if not hasattr(pos, "get_stock_amount_dict") or not hasattr(pos, "get_stock_price"):
            continue
        date_str = str(date_key)
        try:
            amounts = pos.get_stock_amount_dict() or {}
        except Exception:
            continue

        for code, prev_count in prev_amounts.items():
            cur = amounts.get(code, 0)
            delta = prev_count - cur
            if delta > 1e-9:
                # 清仓/减仓：用当日价（若已清仓则用上日价近似）
                price = float(pos.get_stock_price(code)) if code in amounts else prev_prices.get(code, 0.0)
                value = price * delta
                trades.append({
                    "date": date_str, "action": "SELL", "code": code, "price": round(price, 4),
                    "quantity": round(delta, 4), "total": round(value, 2),
                    "cost": round(value * cost_sell, 2),
                })
        for code, cur_count in amounts.items():
            prev_count = prev_amounts.get(code, 0)
            delta = cur_count - prev_count
            if delta > 1e-9:
                price = float(pos.get_stock_price(code))
                value = price * delta
                trades.append({
                    "date": date_str, "action": "BUY", "code": code, "price": round(price, 4),
                    "quantity": round(delta, 4), "total": round(value, 2),
                    "cost": round(value * cost_buy, 2),
                })
        prev_amounts = amounts
        prev_prices = {c: float(pos.get_stock_price(c)) for c in amounts}

    trades.sort(key=lambda t: (t["date"], t["action"], t["code"]))
    return trades


def normalize_benchmark(code: str = None) -> str:
    """将 benchmark 代码归一化为 qlib 格式（SH000300）。

    前端/用户常传 "000300.SH"（Wind 风格），qlib 数据 instruments 用 "SH000300"。
    """
    code = code or settings.quant.get("benchmark", "SH000300")
    if code and "." in code:
        sym, market = code.split(".", 1)
        if market.upper() in ("SH", "SZ", "BJ"):
            return f"{market.upper()}{sym}"
    return code


def run_qlib_backtest(
    score_df: pd.DataFrame,
    start: str = None,
    end: str = None,
    topk: int = None,
    n_drop: int = None,
    benchmark: str = None,
    rebalance_freq: str = "day",
    portfolio_method: str = None,
) -> dict:
    """用 QLib backtest_daily 运行 top-k dropout 回测。

    QLib Exchange 原生处理 A 股约束：
    - 涨跌停: limit_threshold=0.095（涨停不可买、跌停不可卖）
    - T+1: deal_price + signal 时序（T日决策，T+1成交）
    - 停牌: only_tradable=True 自动过滤
    - 交易成本: open_cost/close_cost/min_cost

    Args:
        score_df: MultiIndex (datetime, instrument) 含 'score' 列
        rebalance_freq: day/week/month（通过 hold_thresh 控制非调仓日持仓）
        portfolio_method: 兼容现有接口；QLib 后端用 TopkDropout 等权
    Returns:
        与 run_backtest 相同格式: {returns, benchmark, turnover, portfolios,
        start_date, end_date, topk, n_drop, rebalance_freq, benchmark_code, portfolio_method}
    """
    init_qlib()
    from qlib.contrib.evaluate import backtest_daily
    from qlib.contrib.strategy.signal_strategy import TopkDropoutStrategy

    period = settings.quant.get("default_backtest_period", {})
    start = start or period.get("start", "2020-01-01")
    end = end or period.get("end", "2024-12-31")
    topk = topk or settings.quant.get("topk", 50)
    n_drop = n_drop or settings.quant.get("n_drop", 5)
    benchmark = normalize_benchmark(benchmark)
    cost_buy = settings.quant.get("cost_buy", 0.0013)
    cost_sell = settings.quant.get("cost_sell", 0.0023)
    slippage_bps = settings.quant.get("slippage_bps", 0)

    # 准备 signal: QLib 要求 DataFrame, index=(datetime, instrument), 含 score 列
    signal = score_df.copy()
    if "score" not in signal.columns:
        raise ValueError("score_df 必须含 'score' 列")
    mask = (signal.index.get_level_values("datetime") >= pd.Timestamp(start)) & \
           (signal.index.get_level_values("datetime") <= pd.Timestamp(end))
    signal = signal[mask]
    if signal.empty:
        raise ValueError("打分数据为空")

    # 防御性过滤北交所（与 run_backtest 对齐）
    include_bj = settings.quant.get("include_bj", False)
    if not include_bj:
        inst = signal.index.get_level_values("instrument")
        bj_mask = inst.str.startswith(("bj", "BJ"))
        if bj_mask.any():
            signal = signal[~bj_mask]

    # hold_thresh: 非调仓日保持持仓的天数
    hold_thresh = {"day": 1, "week": 5, "month": 20}.get(rebalance_freq, 1)

    strategy_obj = TopkDropoutStrategy(
        topk=topk,
        n_drop=n_drop,
        signal=signal,
        method_sell="bottom",
        method_buy="top",
        hold_thresh=hold_thresh,
        only_tradable=True,
    )

    # A 股交易约束
    exchange_kwargs = {
        "freq": "day",
        "limit_threshold": 0.095,   # A 股涨跌停 9.5%
        "deal_price": "close",       # T 日决策，T+1 收盘成交（无未来函数）
        "open_cost": cost_buy,
        "close_cost": cost_sell,
        "min_cost": 5,
    }
    if slippage_bps > 0:
        exchange_kwargs["impact_cost"] = slippage_bps / 10000.0

    backtest_params = {
        "start_time": start,
        "end_time": end,
        "account": 100000000,
        "benchmark": benchmark,
        "exchange_kwargs": exchange_kwargs,
    }

    logger.info("QLib 回测: %s~%s topk=%d n_drop=%d freq=%s benchmark=%s",
                start, end, topk, n_drop, rebalance_freq, benchmark)

    report_normal, positions_normal = backtest_daily(
        strategy=strategy_obj, **backtest_params
    )

    # 转换为与 run_backtest 兼容的输出格式
    returns = report_normal["return"].dropna() if "return" in report_normal else pd.Series(dtype=float)
    bench = report_normal.get("bench")
    if bench is not None:
        bench = bench.dropna()
    turnover = float(report_normal["turnover"].mean()) if "turnover" in report_normal else None

    # 逐笔成交明细：从持仓快照差分还原 BUY/SELL 动作
    trades = []
    try:
        trades = extract_trades_from_positions(positions_normal)
    except Exception as e:
        logger.warning("还原成交明细失败，trades 置空: %s", e)

    # portfolios: 前5个调仓日持仓快照（与 run_backtest 对齐）
    portfolios = []
    for date_key, pos in _position_dates_mapping(positions_normal)[:5]:
        holdings = {}
        try:
            # QLib Position 对象: get_stock_list() 返回 {instrument: amount}
            stock_list = pos.get_stock_list() if hasattr(pos, "get_stock_list") else {}
            for inst_key, amount in stock_list.items():
                holdings[str(inst_key)] = float(amount)
        except Exception as e:
            logger.debug("解析持仓失败 date=%s: %s", date_key, e)
        portfolios.append({"date": str(date_key), "holdings": holdings})

    return {
        "returns": returns,
        "benchmark": bench,
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
