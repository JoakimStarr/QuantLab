"""组合绩效指标计算：夏普/索提诺/最大回撤/卡玛/年化/胜率。

使用 empyrical 库计算标准量化指标，替代自研实现。
"""
import numpy as np
import pandas as pd
from empyrical import (
    annual_return as _emp_annual_return,
    annual_volatility as _emp_annual_volatility,
    calmar_ratio as _emp_calmar,
    max_drawdown as _emp_max_drawdown,
    sharpe_ratio as _emp_sharpe,
    sortino_ratio as _emp_sortino,
)

# freq -> empyrical period 映射
_FREQ_MAP = {"day": "daily", "week": "weekly", "month": "monthly"}


def _to_series(returns) -> pd.Series:
    if isinstance(returns, pd.DataFrame):
        returns = returns.iloc[:, 0]
    return pd.Series(returns).dropna()


def _to_period(freq: str) -> str:
    return _FREQ_MAP.get(freq, "daily")


def _nan_to_none(v):
    """将 np.nan 转为 None，保持与原有接口兼容。"""
    return None if (v is None or (isinstance(v, float) and np.isnan(v))) else v


def sharpe_ratio(returns, freq: str = "day") -> float:
    s = _to_series(returns)
    if len(s) < 2:
        return None
    return _nan_to_none(float(_emp_sharpe(s, period=_to_period(freq))))


def sortino_ratio(returns, freq: str = "day") -> float:
    s = _to_series(returns)
    if len(s) < 2:
        return None
    return _nan_to_none(float(_emp_sortino(s, period=_to_period(freq))))


def max_drawdown(returns) -> float:
    s = _to_series(returns)
    if len(s) < 2:
        return None
    return _nan_to_none(float(_emp_max_drawdown(s)))


def annual_return(returns, freq: str = "day") -> float:
    s = _to_series(returns)
    if len(s) < 2:
        return None
    return _nan_to_none(float(_emp_annual_return(s, period=_to_period(freq))))


def annual_volatility(returns, freq: str = "day") -> float:
    s = _to_series(returns)
    if len(s) < 2:
        return None
    return _nan_to_none(float(_emp_annual_volatility(s, period=_to_period(freq))))


def win_rate(returns) -> float:
    s = _to_series(returns)
    if len(s) < 1:
        return None
    return float((s > 0).sum() / len(s))


def calmar_ratio(returns) -> float:
    s = _to_series(returns)
    if len(s) < 2:
        return None
    return _nan_to_none(float(_emp_calmar(s, period="daily")))


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
            (annual_return(returns) - bench_ar) if (result["annual_return"]
                                                    is not None and bench_ar is not None) else None
        )
    return result


def build_nav_curve(returns, benchmark_returns=None) -> dict:
    """构建净值曲线（归一化到 1.0）。"""
    s = _to_series(returns)
    nav = (1 + s).cumprod()
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
        b_nav = b_nav.reindex(nav.index, method="ffill")
        curve["benchmark"] = [round(float(v), 4) if not np.isnan(v) else None for v in b_nav.values]
    return curve


def _round(v, ndigits=4):
    return round(v, ndigits) if v is not None else None
