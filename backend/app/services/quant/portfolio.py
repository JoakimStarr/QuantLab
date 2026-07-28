"""组合绩效指标计算：夏普/索提诺/最大回撤/卡玛/年化/胜率。

从组合日收益序列计算标准量化指标，不依赖 qlib（纯 pandas/numpy）。
"""
import numpy as np
import pandas as pd

# A 股年化交易日
TRADING_DAYS = 252


def _to_series(returns) -> pd.Series:
    if isinstance(returns, pd.DataFrame):
        # qlib 返回 DataFrame，取第一列
        returns = returns.iloc[:, 0]
    return pd.Series(returns).dropna()


def sharpe_ratio(returns, freq: str = "day") -> float:
    s = _to_series(returns)
    if len(s) < 2 or s.std() == 0:
        return None
    ann = TRADING_DAYS if freq == "day" else 52
    return float(s.mean() / s.std() * np.sqrt(ann))


def sortino_ratio(returns, freq: str = "day") -> float:
    s = _to_series(returns)
    if len(s) < 2:
        return None
    downside = s[s < 0]
    if len(downside) < 1 or downside.std() == 0:
        return None
    ann = TRADING_DAYS if freq == "day" else 52
    return float(s.mean() / downside.std() * np.sqrt(ann))


def max_drawdown(returns) -> float:
    s = _to_series(returns)
    if len(s) < 2:
        return None
    cum = (1 + s).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    return float(dd.min())


def annual_return(returns, freq: str = "day") -> float:
    s = _to_series(returns)
    if len(s) < 2:
        return None
    cum = (1 + s).prod()
    ann = TRADING_DAYS if freq == "day" else 52
    years = len(s) / ann
    if years <= 0:
        return None
    return float(cum ** (1 / years) - 1)


def annual_volatility(returns, freq: str = "day") -> float:
    s = _to_series(returns)
    if len(s) < 2:
        return None
    ann = TRADING_DAYS if freq == "day" else 52
    return float(s.std() * np.sqrt(ann))


def win_rate(returns) -> float:
    s = _to_series(returns)
    if len(s) < 1:
        return None
    return float((s > 0).sum() / len(s))


def calmar_ratio(returns) -> float:
    ar = annual_return(returns)
    mdd = max_drawdown(returns)
    if ar is None or mdd is None or mdd == 0:
        return None
    return float(ar / abs(mdd))


def analyze_portfolio(returns, benchmark_returns=None) -> dict:
    """计算组合完整绩效指标。"""
    result = {
        "annual_return": _round(annual_return(returns)),
        "annual_volatility": _round(annual_volatility(returns)),
        "sharpe": _round(sharpe_ratio(returns)),
        "sortino": _round(sortino_ratio(returns)),
        "max_drawdown": _round(max_drawdown(returns)),
        "calmar": _round(calmar_ratio(returns)),
        "win_rate": _round(win_rate(returns)),
    }
    if benchmark_returns is not None:
        bench_ar = annual_return(benchmark_returns)
        result["benchmark_return"] = _round(bench_ar)
        result["excess_return"] = _round(
            (annual_return(returns) - bench_ar) if (result["annual_return"] is not None and bench_ar is not None) else None
        )
    return result


def build_nav_curve(returns, benchmark_returns=None) -> dict:
    """构建净值曲线（归一化到 1.0）。"""
    s = _to_series(returns)
    nav = (1 + s).cumprod()
    # 兼容 DatetimeIndex 与普通索引
    if hasattr(nav.index, "date"):
        dates = [str(d.date()) for d in nav.index]
    else:
        dates = [str(i) for i in nav.index]
    curve = {
        "dates": dates,
        "portfolio": [round(float(v), 4) for v in nav.values],
    }
    if benchmark_returns is not None:
        b = _to_series(benchmark_returns)
        b_nav = (1 + b).cumprod()
        # 对齐长度
        b_nav = b_nav.reindex(nav.index, method="ffill")
        curve["benchmark"] = [round(float(v), 4) if not np.isnan(v) else None for v in b_nav.values]
    return curve


def _round(v, ndigits=4):
    return round(v, ndigits) if v is not None else None
