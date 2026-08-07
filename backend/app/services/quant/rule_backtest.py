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


def _cross_above(a: pd.Series, b) -> pd.Series:
    """a 上穿 b（b 可为 Series 或标量阈值；标量阈值的前一日值即其本身）。"""
    b_prev = b.shift(1) if hasattr(b, "shift") else b
    return (a > b) & (a.shift(1) <= b_prev)


def _cross_below(a: pd.Series, b) -> pd.Series:
    """a 下穿 b（b 可为 Series 或标量阈值）。"""
    b_prev = b.shift(1) if hasattr(b, "shift") else b
    return (a < b) & (a.shift(1) >= b_prev)


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


# ---------------------------------------------------------------- 指标线（K线图叠加）
def _build_indicator(template_key: str, close: pd.Series, p: dict) -> dict | None:
    """返回策略所用技术指标的曲线（供前端 K 线图叠加）。

    grid=main 为价格域线（叠加在主图），grid=sub 为振荡域线（叠加在副图）。
    dates 与 values 一一对应（与回测 close 交易日对齐），供前端按日期匹配 K 线。
    配对策略为双标的价差驱动，K 线主图叠加无意义，返回 None。
    """
    def _lines(items: list[tuple[str, str, str, pd.Series]], dates: list[str]) -> dict:
        return {
            "template": template_key,
            "name": TEMPLATES[template_key]["name"],
            "dates": dates,
            "lines": [
                {
                    "key": key, "name": name, "grid": grid,
                    "values": [round(float(v), 4) if v == v else None for v in vals],
                }
                for key, name, grid, vals in items
            ],
        }

    dates = [str(d.date()) for d in close.index]

    if template_key == "bollinger":
        mid = close.rolling(int(p["window"])).mean()
        std = close.rolling(int(p["window"])).std()
        return _lines([
            ("mid", "中轨", "main", mid),
            ("upper", "上轨", "main", mid + float(p["k"]) * std),
            ("lower", "下轨", "main", mid - float(p["k"]) * std),
        ], dates)
    if template_key == "ma_cross":
        fa = close.rolling(int(p["fast"])).mean()
        sa = close.rolling(int(p["slow"])).mean()
        return _lines([
            ("fast", f"快线MA{p['fast']}", "main", fa),
            ("slow", f"慢线MA{p['slow']}", "main", sa),
        ], dates)
    if template_key == "rsi":
        import vectorbt as vbt
        rsi = vbt.RSI.run(close, window=int(p["period"])).rsi
        return _lines([("rsi", f"RSI{p['period']}", "sub", rsi)], dates)
    if template_key == "ma_alignment":
        s = close.rolling(int(p["short"])).mean()
        m = close.rolling(int(p["mid"])).mean()
        ln = close.rolling(int(p["long"])).mean()
        return _lines([
            ("short", f"短期MA{p['short']}", "main", s),
            ("mid", f"中期MA{p['mid']}", "main", m),
            ("long", f"长期MA{p['long']}", "main", ln),
        ], dates)
    if template_key == "macd":
        import vectorbt as vbt
        macd = vbt.MACD.run(close, fast_window=int(p["fast"]), slow_window=int(p["slow"]),
                            signal_window=int(p["signal"]))
        return _lines([
            ("dif", "DIF", "sub", macd.macd),
            ("dea", "DEA", "sub", macd.signal),
        ], dates)
    if template_key == "momentum":
        w = int(p["window"])
        return _lines([
            ("upper", f"{w}日新高", "main", close.rolling(w).max().shift(1)),
            ("lower", f"{w}日新低", "main", close.rolling(w).min().shift(1)),
        ], dates)
    return None


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


def _run_single_vbt(close: pd.Series, entries: pd.Series, exits: pd.Series, fees: float,
                    code: str, init_cash: float) -> tuple:
    import vectorbt as vbt

    pf = vbt.Portfolio.from_signals(
        close,
        entries=entries,
        exits=exits,
        size=1.0,
        size_type="Percent",
        direction="longonly",
        cash_sharing=True,
        fees=fees,
        freq="d",
        init_cash=init_cash,
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
                # vbt orders 的方向在 Side 列（Buy/Sell），Size 恒为正；
                # 兼容旧版本（无 Side 列时回退用 Size 正负判断）
                side = str(row.get("Side", "")).strip().lower()
                action = "BUY" if side == "buy" else ("SELL" if side == "sell" else ("BUY" if size > 0 else "SELL"))
                trades.append({
                    "date": dt, "action": action, "code": code,
                    "price": round(price, 4), "quantity": round(abs(size), 4),
                    "total": round(abs(size) * price, 2), "cost": round(abs(fee), 4),
                })
    except Exception as e:  # noqa: BLE001
        logger.warning("提取成交明细失败: %s", e)
    trades.sort(key=lambda t: (t["date"], t["action"]))
    return returns, trades[:2000]


def _run_pairs_manual(a: pd.Series, b: pd.Series, entries: pd.Series, exits: pd.Series, fees: float,
                      spread: pd.Series, code: str) -> tuple:
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
            trades.append({"date": str(d.date()), "action": "BUY", "code": code,
                           "price": round(float(a.loc[d]), 4),
                           "quantity": 1.0, "total": round(float(a.loc[d]), 2), "cost": round(fees, 4)})
        elif bool(exits.loc[d]):
            trades.append({"date": str(d.date()), "action": "SELL", "code": code,
                           "price": round(float(a.loc[d]), 4),
                           "quantity": 1.0, "total": round(float(a.loc[d]), 2), "cost": round(fees, 4)})
    return strategy, trades[:2000]


def run_rule_backtest(template_key: str, params: dict | None, symbols: list[str],
                      start: str, end: str, benchmark: str = "SH000300",
                      initial_capital: float = 10_000_000) -> dict:
    """运行规则策略回测，返回指标/净值/交易记录（与因子回测同构）。

    initial_capital 为初始资金（元），默认 1000 万，非正数回退默认。
    """
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
    capital = float(initial_capital or 0)
    if capital <= 0:
        capital = 10_000_000

    if tpl["kind"] == "pairs":
        a, b = prices[codes[0]], prices[codes[1]]
        entries, exits, spread = _pairs_signals(a, b, p)
        returns, trades = _run_pairs_manual(a, b, entries, exits, fees, spread, codes[0])
        symbols_shown = codes
        indicator = None
    else:
        close = prices[codes[0]]
        gen_name = _GENERATORS[template_key]
        generator = globals().get(gen_name)
        entries, exits = generator(close, p)
        returns, trades = _run_single_vbt(close, entries, exits, fees, codes[0], capital)
        symbols_shown = [codes[0]]
        indicator = _build_indicator(template_key, close, p)

    bench_ret = _load_benchmark(D, benchmark, start, end)
    if returns.empty:
        raise ValueError("回测区间内无有效数据（可能是数据不足或信号为空）")

    from app.services.quant.portfolio import analyze_portfolio, build_nav_curve
    metrics = analyze_portfolio(returns, bench_ret)
    metrics["initial_capital"] = capital
    metrics["indicator"] = indicator
    nav_curve = build_nav_curve(returns, bench_ret)

    # 与因子回测结果同构：指标展开到顶层（组件/列表页通用字段），同时保留 metrics 嵌套。
    # 规则策略 v1 不持久化，无 id/初始资金/调仓频率等持久化字段，统一置空由前端降级展示。
    result = {
        "ok": True,
        "template": template_key,
        "name": tpl["name"],
        "category": tpl.get("category"),
        "kind": tpl.get("kind"),
        "symbols": symbols_shown,
        "benchmark": benchmark,
        "start_date": start,
        "end_date": end,
        "initial_capital": capital,
        "indicator": indicator,
        "metrics": metrics,
        "nav_curve": nav_curve,
        "trades": trades,
        "n_trades": len(trades),
        "params": {k: v for k, v in p.items() if k in {pc["key"] for pc in tpl["params"]}},
    }
    result.update({k: metrics.get(k) for k in (
        "annual_return", "annual_volatility", "sharpe", "sortino",
        "max_drawdown", "calmar", "win_rate", "benchmark_return", "excess_return",
    )})
    return result
