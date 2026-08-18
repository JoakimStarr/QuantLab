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


def total_return(returns) -> float:
    """区间累计收益率（净值终值 - 1）。"""
    s = _to_series(returns)
    if s.empty:
        return None
    return _nan_to_none(float((1 + s).prod() - 1))


def beta_ratio(returns, benchmark_returns) -> float:
    """贝塔：日收益协方差 / 基准方差。"""
    s, b = _to_series(returns).align(_to_series(benchmark_returns), join="inner")
    if len(s) < 2:
        return None
    var = float(b.var())
    if var == 0 or np.isnan(var):
        return None
    return _nan_to_none(float(s.cov(b) / var))


def alpha_ratio(returns, benchmark_returns) -> float:
    """阿尔法（年化，无风险利率取 0）：年化收益 - beta * 基准年化收益。"""
    s, b = _to_series(returns).align(_to_series(benchmark_returns), join="inner")
    if s.empty:
        return None
    ar = annual_return(s)
    br = annual_return(b)
    beta = beta_ratio(s, b)
    if ar is None or br is None or beta is None:
        return None
    return _nan_to_none(float(ar - beta * br))


def information_ratio(returns, benchmark_returns) -> float:
    """信息比率：日均超额 / 超额日波动，年化 252。"""
    s, b = _to_series(returns).align(_to_series(benchmark_returns), join="inner")
    excess = s - b
    excess = excess.dropna()
    if len(excess) < 2:
        return None
    std = float(excess.std())
    if std == 0 or np.isnan(std):
        return None
    return _nan_to_none(float(excess.mean() / std * np.sqrt(252)))


def daily_mean_excess(returns, benchmark_returns) -> float:
    """日均超额收益。"""
    s, b = _to_series(returns).align(_to_series(benchmark_returns), join="inner")
    excess = (s - b).dropna()
    if excess.empty:
        return None
    return _nan_to_none(float(excess.mean()))


def excess_max_drawdown(returns, benchmark_returns) -> float:
    """超额收益（策略-基准净值差）的最大回撤。"""
    s, b = _to_series(returns).align(_to_series(benchmark_returns), join="inner")
    excess = (s - b).dropna()
    if excess.empty:
        return None
    return _nan_to_none(float(max_drawdown(excess)))


def excess_sharpe_ratio(returns, benchmark_returns) -> float:
    """超额收益夏普比率。"""
    s, b = _to_series(returns).align(_to_series(benchmark_returns), join="inner")
    excess = (s - b).dropna()
    if excess.empty:
        return None
    return _nan_to_none(float(sharpe_ratio(excess)))


def win_loss_count(returns) -> tuple:
    """（盈利天数, 亏损天数），按日收益口径。"""
    s = _to_series(returns).dropna()
    return int((s > 0).sum()), int((s < 0).sum())


def profit_loss_ratio(returns) -> float:
    """盈亏比：平均盈利日收益 / 平均亏损日收益（绝对值）。"""
    s = _to_series(returns).dropna()
    wins = s[s > 0]
    losses = s[s < 0]
    if wins.empty or losses.empty:
        return None
    avg_loss = abs(float(losses.mean()))
    if avg_loss == 0:
        return None
    return _nan_to_none(float(wins.mean() / avg_loss))


def max_drawdown_period(returns) -> str:
    """最大回撤区间：峰值日,谷底日（日期字符串，无回撤返回 None）。"""
    s = _to_series(returns).dropna()
    if s.empty:
        return None
    nav = (1 + s).cumprod()
    peak = nav.cummax()
    dd = nav / peak - 1
    trough_idx = dd.idxmin()
    peak_idx = nav.loc[:trough_idx].idxmax()
    if float(dd.loc[trough_idx]) >= 0:
        return None

    def _fmt(i):
        return str(i.date()) if hasattr(i, "date") else str(i)

    return "{},{}".format(_fmt(peak_idx), _fmt(trough_idx))


def analyze_portfolio(returns, benchmark_returns=None) -> dict:
    """计算组合完整绩效指标。

    基础指标无条件计算；基准相关指标（alpha/beta/信息比率/超额系）仅当
    benchmark_returns 提供时输出。字段只增不删，向后兼容旧结果。
    """
    result = {
        "annual_return": _round(annual_return(returns)),
        "annual_volatility": _round(annual_volatility(returns)),
        "sharpe": _round(sharpe_ratio(returns)),
        "sortino": _round(sortino_ratio(returns)),
        "max_drawdown": _round(max_drawdown(returns)),
        "calmar": _round(calmar_ratio(returns)),
        "win_rate": _round(win_rate(returns)),
        # 聚宽口径扩展：区间/回撤区间/盈亏统计
        "total_return": _round(total_return(returns)),
        "max_drawdown_period": max_drawdown_period(returns),
        "profit_loss_ratio": _round(profit_loss_ratio(returns)),
    }
    win_cnt, loss_cnt = win_loss_count(returns)
    result["win_count"] = win_cnt
    result["loss_count"] = loss_cnt
    if benchmark_returns is not None:
        bench_ar = annual_return(benchmark_returns)
        result["benchmark_return"] = _round(bench_ar)
        result["excess_return"] = _round(
            (annual_return(returns) - bench_ar) if (result["annual_return"]
                                                    is not None and bench_ar is not None) else None
        )
        # 聚宽口径扩展：alpha/beta/信息比率/超额系/基准波动率
        result["alpha"] = _round(alpha_ratio(returns, benchmark_returns))
        result["beta"] = _round(beta_ratio(returns, benchmark_returns))
        result["information_ratio"] = _round(information_ratio(returns, benchmark_returns))
        result["daily_mean_excess"] = _round(daily_mean_excess(returns, benchmark_returns))
        result["excess_max_drawdown"] = _round(excess_max_drawdown(returns, benchmark_returns))
        result["excess_sharpe"] = _round(excess_sharpe_ratio(returns, benchmark_returns))
        result["benchmark_volatility"] = _round(annual_volatility(benchmark_returns))
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
