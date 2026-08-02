"""组合绩效报告：empyrical 为核心指标 + quantstats 可选 HTML tear-sheet。

收敛说明：
- 指标计算统一走 empyrical（纯 numpy，更快更轻），不再用 quantstats 的重复实现；
- quantstats 仅保留 HTML tear-sheet 报表能力（独特价值），缺失时自动降级跳过。
"""
import logging
import os

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# HTML 报表输出目录（相对 backend 运行目录；由 settings 数据目录派生）
_REPORT_DIR = os.environ.get("REPORT_DIR", "static/reports")

# empyrical period: 日收益 → "daily"
_PERIOD = "daily"
_ANNUAL = 252


def _as_daily_series(returns) -> pd.Series:
    """归一化输入为按日期的收益序列。"""
    if returns is None:
        return pd.Series(dtype=float)
    s = pd.Series(returns)
    if s.index.inferred_type not in ("datetime64", "datetime"):
        try:
            s.index = pd.to_datetime(s.index)
        except Exception:
            pass
    s = s.sort_index()
    s.name = "returns"
    return s


def generate_portfolio_report(
    returns,
    benchmark=None,
    title: str = "QuantLab 组合绩效报告",
    generate_html: bool = True,
    html_filename: str = None,
) -> dict:
    """生成组合绩效报告。

    Args:
        returns: 组合日收益序列（pd.Series，index=日期）
        benchmark: 基准日收益序列（可选）
        title: 报表标题
        generate_html: 是否生成 HTML tear-sheet（quantstats，可选）
        html_filename: HTML 文件名（默认 <title>.html，写入 static/reports/）

    Returns:
        {
            "metrics": {...empyrical 指标...},
            "html_report": "/reports/xxx.html" | None,
            "n_obs": int,
            "start_date": str, "end_date": str,
        }
    """
    ret = _as_daily_series(returns)
    if ret.empty:
        return {"metrics": {}, "html_report": None,
                "n_obs": 0, "start_date": None, "end_date": None,
                "error": "收益序列为空"}

    bench = _as_daily_series(benchmark) if benchmark is not None else None

    metrics = _empyrical_metrics(ret, bench)

    html_report = None
    if generate_html:
        html_report = _write_html_report(ret, bench, title, html_filename)

    return {
        "metrics": metrics,
        "html_report": html_report,
        "n_obs": int(len(ret)),
        "start_date": str(ret.index.min().date()),
        "end_date": str(ret.index.max().date()),
    }


def _empyrical_metrics(ret: pd.Series, bench: pd.Series | None) -> dict:
    """empyrical：全量核心绩效指标（纯 numpy，快）。"""
    from empyrical import (
        annual_return, annual_volatility, calmar_ratio, max_drawdown,
        sharpe_ratio, sortino_ratio, tail_ratio, value_at_risk,
        conditional_value_at_risk, alpha as emp_alpha, beta as emp_beta,
    )
    from scipy.stats import skew as _skew, kurtosis as _kurtosis

    stats = {}
    try:
        # 核心指标
        stats["sharpe"] = _f(sharpe_ratio(ret, period=_PERIOD))
        stats["sortino"] = _f(sortino_ratio(ret, period=_PERIOD))
        stats["calmar"] = _f(calmar_ratio(ret))
        stats["cagr"] = _f(annual_return(ret, period=_PERIOD))
        stats["annual_volatility"] = _f(annual_volatility(ret, period=_PERIOD))
        stats["max_drawdown"] = _f(max_drawdown(ret))
        stats["skew"] = _f(float(_skew(ret)) if len(ret) > 2 else None)
        stats["kurtosis"] = _f(float(_kurtosis(ret)) if len(ret) > 3 else None)
        stats["var_95"] = _f(value_at_risk(ret, cutoff=0.05))
        stats["cvar_95"] = _f(conditional_value_at_risk(ret, cutoff=0.05))
        stats["tail_ratio"] = _f(tail_ratio(ret))

        # 简单统计
        stats["win_rate"] = _f(float((ret > 0).mean()))
        stats["avg_return"] = _f(float(ret.mean()))
        stats["expected_daily_return"] = _f(float(ret.mean()))
        stats["best_day"] = _f(float(ret.max()))
        stats["worst_day"] = _f(float(ret.min()))
        stats["best_week"] = _f(float(ret.resample("W").apply(cum_ret).max()))
        stats["worst_week"] = _f(float(ret.resample("W").apply(cum_ret).min()))
        stats["best_month"] = _f(float(ret.resample("ME").apply(cum_ret).max()))
        stats["worst_month"] = _f(float(ret.resample("ME").apply(cum_ret).min()))
        stats["consecutive_wins"], stats["consecutive_losses"] = _consecutive_streaks(ret)
    except Exception as e:
        logger.warning("empyrical 指标计算失败: %s", e)

    # 基准相关
    if bench is not None and len(bench) > 10:
        try:
            aligned = pd.concat([ret, bench], axis=1).dropna()
            if len(aligned) >= 20:
                r = aligned.iloc[:, 0]
                b = aligned.iloc[:, 1]
                stats["beta"] = _f(emp_beta(r, b, risk_free=0.0))
                stats["alpha"] = _f(emp_alpha(r, b, risk_free=0.0))
                stats["rsq"] = _f(float(r.corr(b) ** 2) if len(r) > 1 else None)
                stats["correlation"] = _f(float(r.corr(b)) if len(r) > 1 else None)
        except Exception:
            pass

    # 过滤 None（对外输出保持一致：不存在的键省略）
    return {k: v for k, v in stats.items() if v is not None}


def cum_ret(x: pd.Series) -> float:
    """窗口累计收益（用于周/月 resample）。"""
    return float((1 + x).prod() - 1)


def _consecutive_streaks(ret: pd.Series) -> tuple:
    """演示连涨/连亏天数。"""
    mask = ret > 0
    win = loss = cur = 0
    cur_type = None
    for b in mask:
        if b and cur_type == True and cur > 0:
            cur += 1
        elif b:
            cur, cur_type = 1, True
        elif cur_type == False and cur > 0:
            cur += 1
        else:
            cur, cur_type = 1, False
        if cur_type is True:
            win = max(win, cur)
        else:
            loss = max(loss, cur)
    return win, loss


def _f(v):
    """round / nan → None。"""
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return None
    return round(float(v), 6) if isinstance(v, (int, float, np.number)) else v


def _write_html_report(ret: pd.Series, bench: pd.Series | None, title: str,
                       filename: str | None) -> str | None:
    """quantstats HTML tear-sheet（可选，不可用时降级跳过）。"""
    try:
        import quantstats as qs
    except ImportError:
        logger.warning("quantstats 未安装，跳过 HTML 报表生成（指标已由 empyrical 计算）")
        return None
    try:
        os.makedirs(_REPORT_DIR, exist_ok=True)
        fname = filename or f"report_{pd.Timestamp.now():%Y%m%d_%H%M%S}.html"
        path = os.path.join(_REPORT_DIR, fname)
        bench_arg = bench if bench is not None else "SPY"
        qs.reports.html(ret, benchmark=bench_arg, title=title, output=path)
        return f"/reports/{fname}"
    except Exception as e:
        logger.warning("quantstats HTML 报表生成失败: %s", e)
        return None