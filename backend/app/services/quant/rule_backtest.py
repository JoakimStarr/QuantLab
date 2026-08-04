"""规则/信号型策略回测服务（策略库 v1）。

与因子 top-k 组合回测不同，本模块由技术指标直接生成 entries/exits 信号：
- 单标的（布林带/双均线/RSI/均线排列/MACD/动量突破）→ vbt.Portfolio.from_signals 做 long/flat
- 配对交易 → 滚动 β 对冲价差，手工算持仓收益

v1 不持久化：运行即返回指标/净值/交易记录，供前端展示。
回测是阻塞计算，调用方必须经 run_in_executor / run_io_cpu 放入线程池。
"""
import logging

import numpy as np
import pandas as pd

from app.core.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- 模板注册表
# params 为前端动态表单 schema；gen 为信号生成器（仅代码内引用，不返回前端）
TEMPLATES: dict[str, dict] = {
    "bollinger": {
        "key": "bollinger",
        "name": "布林带",
        "category": "均值回归",
        "kind": "single",
        "description": "收盘价下穿下轨买入，上穿中轨卖出。适合震荡市抓波段。",
        "params": [
            {"key": "window", "label": "窗口", "type": "number", "default": 20, "min": 5, "max": 120},
            {"key": "k", "label": "标准差倍数", "type": "number", "default": 2.0, "min": 0.5, "max": 5, "step": 0.1},
        ],
        "default_symbols": 1,
        "gen": "_bollinger_signals",
    },
    "ma_cross": {
        "key": "ma_cross",
        "name": "双均线",
        "category": "趋势",
        "kind": "single",
        "description": "快线上穿慢线买入（金叉），下穿卖出（死叉）。经典趋势策略。",
        "params": [
            {"key": "fast", "label": "快线", "type": "number", "default": 10, "min": 2, "max": 60},
            {"key": "slow", "label": "慢线", "type": "number", "default": 30, "min": 5, "max": 250},
        ],
        "default_symbols": 1,
        "gen": "_ma_cross_signals",
    },
    "rsi": {
        "key": "rsi",
        "name": "RSI超买超卖",
        "category": "均值回归",
        "kind": "single",
        "description": "RSI 从超卖区回升买入，进入超买区卖出。",
        "params": [
            {"key": "period", "label": "周期", "type": "number", "default": 14, "min": 3, "max": 60},
            {"key": "oversold", "label": "超卖线", "type": "number", "default": 30, "min": 10, "max": 45},
            {"key": "overbought", "label": "超买线", "type": "number", "default": 70, "min": 55, "max": 90},
        ],
        "default_symbols": 1,
        "gen": "_rsi_signals",
    },
    "ma_alignment": {
        "key": "ma_alignment",
        "name": "均线多头排列",
        "category": "趋势",
        "kind": "single",
        "description": "短期均线高于中期、中期高于长期时持多，排列破坏则离场。",
        "params": [
            {"key": "short", "label": "短期均线", "type": "number", "default": 5, "min": 2, "max": 30},
            {"key": "mid", "label": "中期均线", "type": "number", "default": 10, "min": 5, "max": 60},
            {"key": "long", "label": "长期均线", "type": "number", "default": 20, "min": 10, "max": 120},
        ],
        "default_symbols": 1,
        "gen": "_ma_alignment_signals",
    },
    "macd": {
        "key": "macd",
        "name": "MACD金叉",
        "category": "趋势",
        "kind": "single",
        "description": "MACD 线上穿信号线（金叉）买入，下穿（死叉）卖出。",
        "params": [
            {"key": "fast", "label": "快线", "type": "number", "default": 12, "min": 5, "max": 30},
            {"key": "slow", "label": "慢线", "type": "number", "default": 26, "min": 15, "max": 60},
            {"key": "signal", "label": "信号线", "type": "number", "default": 9, "min": 3, "max": 30},
        ],
        "default_symbols": 1,
        "gen": "_macd_signals",
    },
    "momentum": {
        "key": "momentum",
        "name": "动量突破",
        "category": "趋势",
        "kind": "single",
        "description": "收盘价创 N 日新高买入，跌破 N 日新低卖出（唐奇安通道）。",
        "params": [
            {"key": "window", "label": "突破窗口", "type": "number", "default": 20, "min": 5, "max": 120},
        ],
        "default_symbols": 1,
        "gen": "_momentum_signals",
    },
    "pairs": {
        "key": "pairs",
        "name": "配对交易",
        "category": "统计套利",
        "kind": "pairs",
        "description": "两只同行业股票的价差（滚动 β 对冲）z-score 跌破阈值开仓、回归后平仓。",
        "params": [
            {"key": "window", "label": "价差窗口", "type": "number", "default": 60, "min": 20, "max": 250},
            {"key": "entry_z", "label": "开仓阈值", "type": "number", "default": 2.0, "min": 1.0, "max": 4.0, "step": 0.1},
            {"key": "exit_z", "label": "平仓阈值", "type": "number", "default": 0.5, "min": 0.0, "max": 2.0, "step": 0.1},
        ],
        "default_symbols": 2,
        "gen": "_pairs_signals",
    },
}

# 信号生成器注册（template_key -> 生成器函数名，globals() 取用）
_GENERATORS = {
    "bollinger": "_bollinger_signals",
    "ma_cross": "_ma_cross_signals",
    "rsi": "_rsi_signals",
    "ma_alignment": "_ma_alignment_signals",
    "macd": "_macd_signals",
    "momentum": "_momentum_signals",
}


def list_templates() -> list[dict]:
    """返回可序列化的模板列表（去掉 gen 等代码引用）。"""
    return [{k: v for k, v in t.items() if k != "gen"} for t in TEMPLATES.values()]


# ---------------------------------------------------------------- 工具函数
def _normalize_symbol(s: str) -> str:
    """股票/指数代码归一化为 qlib 小写格式：sh600000 / 600000 / SH000300 → sh000300。"""
    s = str(s or "").strip().lower()
    if not s:
        raise ValueError("标的不能为空")
    if s[:2] in ("sh", "sz", "bj"):
        return s
    if len(s) == 6 and s.isdigit():
        from app.services.data.code_utils import to_qlib_code
        return to_qlib_code(s)
    raise ValueError(f"无法识别的标的代码: {s}")


def _cross_above(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a > b) & (a.shift(1) <= b.shift(1))


def _cross_below(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a < b) & (a.shift(1) >= b.shift(1))


def _fill_false(s: pd.Series) -> pd.Series:
    return s.fillna(False).astype(bool)


# ---------------------------------------------------------------- 信号生成器
def _bollinger_signals(close, p):
    mid = close.rolling(int(p["window"])).mean()
    std = close.rolling(int(p["window"])).std()
    lower = mid - float(p["k"]) * std
    entries = _fill_false(_cross_below(close, lower))
    exits = _fill_false(_cross_above(close, mid))
    return entries, exits


def _ma_cross_signals(close, p):
    fa = close.rolling(int(p["fast"])).mean()
    sa = close.rolling(int(p["slow"])).mean()
    return _fill_false(_cross_above(fa, sa)), _fill_false(_cross_below(fa, sa))


def _rsi_signals(close, p):
    import vectorbt as vbt
    rsi = vbt.RSI.run(close, window=int(p["period"])).rsi
    entries = _fill_false(_cross_above(rsi, float(p["oversold"])))
    exits = _fill_false(_cross_above(rsi, float(p["overbought"])))
    return entries, exits


def _ma_alignment_signals(close, p):
    s = close.rolling(int(p["short"])).mean()
    m = close.rolling(int(p["mid"])).mean()
    l = close.rolling(int(p["long"])).mean()
    pos = _fill_false((s > m) & (m > l))
    entries = pos & ~pos.shift(1, fill_value=False)
    exits = ~pos & pos.shift(1, fill_value=False)
    return entries, exits


def _macd_signals(close, p):
    import vectorbt as vbt
    macd = vbt.MACD.run(close, fast_window=int(p["fast"]), slow_window=int(p["slow"]),
                        signal_window=int(p["signal"]))
    return _fill_false(_cross_above(macd.macd, macd.signal)), _fill_false(_cross_below(macd.macd, macd.signal))


def _momentum_signals(close, p):
    w = int(p["window"])
    high = close.rolling(w).max().shift(1)
    low = close.rolling(w).min().shift(1)
    entries = _fill_false(close > high)
    exits = _fill_false(close < low)
    return entries, exits


def _pairs_signals(a: pd.Series, b: pd.Series, p):
    w = int(p["window"])
    beta = a.rolling(w).cov(b) / b.rolling(w).var()
    spread = a - beta * b
    mean = spread.rolling(w).mean()
    std = spread.rolling(w).std()
    z = (spread - mean) / std
    entries = _fill_false(z < -float(p["entry_z"]))
    exits = _fill_false(z > -float(p["exit_z"]))
    return entries, exits, spread


# ---------------------------------------------------------------- 回测执行
def _load_close(D, codes: list[str], start: str, end: str) -> dict[str, pd.Series]:
    """按 qlib 代码列表加载日频收盘价（宽表 unstack 后逐列取）。"""
    raw = D.features(codes, ["$close"], start_time=start, end_time=end, freq="day")
    if raw is None or raw.empty:
        raise ValueError("标的数据为空，请检查代码与日期范围")
    df = raw["$close"].unstack(level="instrument")
    out = {}
    for code in codes:
        if code not in df.columns:
            raise ValueError(f"{code} 无行情数据")
        s = df[code].dropna()
        if s.empty:
            raise ValueError(f"{code} 在所选区间无数据")
        out[code] = s
    return out


def _load_benchmark(D, benchmark: str, start: str, end: str) -> pd.Series | None:
    try:
        bench_code = _normalize_symbol(benchmark)
        raw = D.features([bench_code], ["$close"], start_time=start, end_time=end, freq="day")
        if raw is None or raw.empty:
            return None
        s = raw["$close"].unstack(level="instrument").iloc[:, 0]
        return s.pct_change().shift(-1).dropna()
    except Exception as e:  # noqa: BLE001
        logger.warning("基准加载失败 %s: %s", benchmark, e)
        return None


def _run_single_vbt(close: pd.Series, entries: pd.Series, exits: pd.Series, fees: float) -> tuple:
    import vectorbt as vbt

    pf = vbt.Portfolio.from_signals(
        close,
        entries=entries,
        exits=exits,
        size=1.0,
        size_type="Value",
        direction="longonly",
        cash_sharing=True,
        fees=fees,
        freq="d",
    )
    returns = pf.returns().dropna()
    returns.name = "return"

    trades = []
    try:
        readable = pf.orders.records_readable
        if hasattr(readable, "empty") and not readable.empty:
            for _, row in readable.iterrows():
                size = float(row.get("Size") or 0)
                price = float(row.get("Price") or np.nan)
                fee = float(row.get("Fees") or 0)
                dt = str(row.get("Timestamp", ""))
                if np.isnan(price) or size == 0:
                    continue
                trades.append({
                    "date": dt, "action": "BUY" if size > 0 else "SELL",
                    "price": round(price, 4), "quantity": round(abs(size), 4),
                    "total": round(abs(size) * price, 2), "cost": round(abs(fee), 2),
                })
    except Exception as e:  # noqa: BLE001
        logger.warning("提取成交明细失败: %s", e)
    trades.sort(key=lambda t: (t["date"], t["action"]))
    return returns, trades[:2000]


def _run_pairs_manual(a: pd.Series, b: pd.Series, entries: pd.Series, exits: pd.Series, fees: float,
                      spread: pd.Series) -> tuple:
    """配对：滚动 β 对冲的日收益 × 持仓（0/1），进出场日按费率扣成本。"""
    ret_a = a.pct_change()
    ret_b = b.pct_change()
    w = max(2, int(len(a) // 2))
    beta = a.rolling(w).cov(b) / b.rolling(w).var()
    hedged = ret_a - beta.shift(1) * ret_b
    pos = (entries.astype(int) - exits.astype(int)).cumsum().clip(0, 1)
    strategy = pos.shift(1).fillna(0) * hedged.fillna(0)
    # 进出场日扣手续费（按对冲后组合市值近似）
    turnover = pos.diff().abs().fillna(0)
    strategy = strategy - turnover * fees
    strategy = strategy.dropna()
    strategy.name = "return"

    trades = []
    for d in spread.index:
        if bool(entries.loc[d]):
            trades.append({"date": str(d.date()), "action": "BUY", "price": round(float(a.loc[d]), 4),
                           "quantity": 1.0, "total": round(float(a.loc[d]), 2), "cost": round(fees, 4)})
        elif bool(exits.loc[d]):
            trades.append({"date": str(d.date()), "action": "SELL", "price": round(float(a.loc[d]), 4),
                           "quantity": 1.0, "total": round(float(a.loc[d]), 2), "cost": round(fees, 4)})
    return strategy, trades[:2000]


def run_rule_backtest(template_key: str, params: dict | None, symbols: list[str],
                      start: str, end: str, benchmark: str = "SH000300") -> dict:
    """运行规则策略回测，返回指标/净值/交易记录（与因子回测同构）。"""
    if template_key not in TEMPLATES:
        raise ValueError(f"未知策略模板: {template_key}")
    tpl = TEMPLATES[template_key]
    # 合并参数：默认值 + 用户参数
    defaults = {p["key"]: p["default"] for p in tpl["params"]}
    p = {**defaults, **(params or {})}
    for pcfg in tpl["params"]:
        if pcfg["type"] == "number":
            p[pcfg["key"]] = float(p.get(pcfg["key"], pcfg["default"]))

    if not symbols or len(symbols) != tpl["default_symbols"]:
        raise ValueError(f"模板「{tpl['name']}」需要 {tpl['default_symbols']} 个标的")
    codes = [_normalize_symbol(s) for s in symbols]

    from app.services.quant.qlib_init import init_qlib
    init_qlib()
    from qlib.data import D

    prices = _load_close(D, codes, start, end)
    fees = float(settings.quant.get("cost_buy", 0.0013))

    if tpl["kind"] == "pairs":
        a, b = prices[codes[0]], prices[codes[1]]
        entries, exits, spread = _pairs_signals(a, b, p)
        returns, trades = _run_pairs_manual(a, b, entries, exits, fees, spread)
        symbols_shown = codes
    else:
        close = prices[codes[0]]
        gen_name = _GENERATORS[template_key]
        generator = globals().get(gen_name)
        entries, exits = generator(close, p)
        returns, trades = _run_single_vbt(close, entries, exits, fees)
        symbols_shown = [codes[0]]

    bench_ret = _load_benchmark(D, benchmark, start, end)
    if returns.empty:
        raise ValueError("回测区间内无有效数据（可能是数据不足或信号为空）")

    from app.services.quant.portfolio import analyze_portfolio, build_nav_curve
    metrics = analyze_portfolio(returns, bench_ret)
    nav_curve = build_nav_curve(returns, bench_ret)

    return {
        "ok": True,
        "template": template_key,
        "name": tpl["name"],
        "symbols": symbols_shown,
        "benchmark": benchmark,
        "metrics": metrics,
        "nav_curve": nav_curve,
        "trades": trades,
        "n_trades": len(trades),
        "params": {k: v for k, v in p.items() if k in {pc["key"] for pc in tpl["params"]}},
    }
