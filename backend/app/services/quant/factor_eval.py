"""因子评价封装：基于 qlib 数据 + pandas 计算 IC/RankIC/ICIR/换手/衰减。

设计：数据通过 qlib D.features 加载，指标用 pandas 手动计算，
避免依赖 qlib.contrib.eval 不稳定 API。
"""
import logging
import numpy as np
import pandas as pd
from app.services.quant.qlib_init import init_qlib, QlibNotAvailableError
from app.core.config import settings

logger = logging.getLogger(__name__)

# 前向收益标签：t 日收盘到 t+1 日收盘的收益（与回测引擎 shift(-1) 口径一致）
# 注意：Ref 负数=未来，label 用未来收益是正确的（预测目标）
_DEFAULT_LABEL = "Ref($close, -1) / $close - 1"

def _load_instruments(market: str) -> list:
    """加载股票池代码列表，默认过滤北交所（bj 开头）股票。

    通过 qlib D.list_instruments 获取成分股列表，
    根据 settings.quant.include_bj 控制是否保留北交所股票。
    """
    from qlib.data import D
    inst_list = D.instruments(market=market)
    code_map = D.list_instruments(inst_list, freq="day")
    codes = sorted(code_map.keys())

    include_bj = settings.quant.get("include_bj", False)
    if not include_bj:
        original_count = len(codes)
        codes = [c for c in codes if not c.lower().startswith("bj")]
        if original_count != len(codes):
            logger.info("过滤北交所股票: %d -> %d", original_count, len(codes))
    return codes


def load_factor_values(
    factor_expr: str,
    start: str,
    end: str,
    universe: str = None,
) -> pd.DataFrame:
    """加载因子值，返回 MultiIndex (datetime, instrument) DataFrame。"""
    init_qlib()
    from qlib.data import D
    market = universe or settings.quant.get("universe", "csi300")
    instruments = _load_instruments(market)
    df = D.features(instruments, [factor_expr], start_time=start, end_time=end, freq="day")
    if df is None or df.empty:
        raise ValueError(f"因子 {factor_expr} 在 {start}~{end} 无数据")
    df = df.rename(columns={df.columns[0]: "factor"})
    return df


def load_label(start: str, end: str, label_expr: str = None, universe: str = None) -> pd.DataFrame:
    """加载前向收益标签。"""
    init_qlib()
    from qlib.data import D
    market = universe or settings.quant.get("universe", "csi300")
    instruments = _load_instruments(market)
    expr = label_expr or _DEFAULT_LABEL
    df = D.features(instruments, [expr], start_time=start, end_time=end, freq="day")
    if df is None or df.empty:
        raise ValueError("标签数据为空")
    return df.rename(columns={df.columns[0]: "label"})


def compute_ic(factor_df: pd.DataFrame, label_df: pd.DataFrame) -> dict:
    """计算 IC/RankIC/ICIR/IR。

    factor_df, label_df: MultiIndex (datetime, instrument)，列名 factor/label
    """
    merged = factor_df.join(label_df, how="inner").dropna()
    if merged.empty:
        return {"ic": None, "rank_ic": None, "icir": None, "ir": None, "n_days": 0}

    # 截面 IC：每日 Pearson 相关
    daily_ic = merged.groupby(level="datetime").apply(
        lambda g: g["factor"].corr(g["label"]) if len(g) >= 2 else np.nan
    ).dropna()
    daily_rank_ic = merged.groupby(level="datetime").apply(
        lambda g: g["factor"].corr(g["label"], method="spearman") if len(g) >= 2 else np.nan
    ).dropna()

    ic_mean = float(daily_ic.mean()) if len(daily_ic) else None
    ic_std = float(daily_ic.std()) if len(daily_ic) > 1 else None
    rank_ic_mean = float(daily_rank_ic.mean()) if len(daily_rank_ic) else None
    rank_ic_std = float(daily_rank_ic.std()) if len(daily_rank_ic) > 1 else None

    icir = float(ic_mean / ic_std) if ic_mean is not None and ic_std else None
    ir = float(rank_ic_mean / rank_ic_std) if rank_ic_mean is not None and rank_ic_std else None

    return {
        "ic": round(ic_mean, 4) if ic_mean is not None else None,
        "rank_ic": round(rank_ic_mean, 4) if rank_ic_mean is not None else None,
        "icir": round(icir, 4) if icir is not None else None,
        "ir": round(ir, 4) if ir is not None else None,
        "n_days": int(len(daily_ic)),
    }


def compute_turnover(factor_df: pd.DataFrame) -> float:
    """计算因子换手率：每日持仓变动均值（按 topk 截面排名近似）。"""
    topk = settings.quant.get("topk", 50)
    # 每日取 topk，计算与前一日的持仓重合度
    daily_topk = factor_df.groupby(level="datetime")["factor"].apply(
        lambda s: set(s.nlargest(topk).index.get_level_values("instrument"))
    )
    turnovers = []
    prev = None
    for date, stocks in daily_topk.items():
        if prev is not None and len(prev) > 0:
            overlap = len(stocks & prev)
            turnover = 1 - overlap / len(prev)
            turnovers.append(turnover)
        prev = stocks
    return round(float(np.mean(turnovers)), 4) if turnovers else None


def compute_decay(factor_df: pd.DataFrame, label_df: pd.DataFrame, max_lag: int = 10) -> dict:
    """计算 IC 衰减：因子与未来 1~max_lag 日收益的 IC 序列。"""
    init_qlib()
    from qlib.data import D
    market = settings.quant.get("universe", "csi300")
    instruments = _load_instruments(market)
    start = factor_df.index.get_level_values("datetime").min()
    end = factor_df.index.get_level_values("datetime").max()

    decay = {}
    for lag in range(1, max_lag + 1):
        # t 到 t+lag 的前向收益（与回测口径一致）
        label_expr = f"Ref($close, -{lag}) / $close - 1"
        try:
            lab = D.features(instruments, [label_expr], start_time=str(start.date()), end_time=str(end.date()), freq="day")
            if lab is None or lab.empty:
                continue
            lab = lab.rename(columns={lab.columns[0]: "label"})
            m = factor_df.join(lab, how="inner").dropna()
            if m.empty:
                continue
            ic = m.groupby(level="datetime").apply(
                lambda g: g["factor"].corr(g["label"]) if len(g) > 5 else np.nan
            ).dropna()
            decay[lag] = round(float(ic.mean()), 4) if len(ic) else None
        except Exception as e:
            logger.debug("decay lag=%s 失败: %s", lag, e)
    return decay


def evaluate_factor(factor_expr: str, start: str, end: str, universe: str = None) -> dict:
    """完整因子评价：IC/RankIC/ICIR/换手/衰减。"""
    factor_df = load_factor_values(factor_expr, start, end, universe)
    label_df = load_label(start, end, universe=universe)
    ic_metrics = compute_ic(factor_df, label_df)
    turnover = compute_turnover(factor_df)
    decay = compute_decay(factor_df, label_df)
    return {
        **ic_metrics,
        "turnover": turnover,
        "decay": decay,
        "eval_start": start,
        "eval_end": end,
        "factor_expr": factor_expr,
    }
