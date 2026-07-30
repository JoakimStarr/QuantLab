"""因子评价封装：基于 qlib 数据 + pandas 计算 IC/RankIC/ICIR/换手/衰减。

设计：数据通过 qlib D.features 加载，指标用 pandas 手动计算，
避免依赖 qlib.contrib.eval 不稳定 API。
"""
import re
import logging
import numpy as np
import pandas as pd
from app.services.quant.qlib_init import init_qlib, QlibNotAvailableError
from app.core.config import settings

logger = logging.getLogger(__name__)

# 前向收益标签：t 日收盘到 t+1 日收盘的收益（与回测引擎 shift(-1) 口径一致）
# 注意：Ref 负数=未来，label 用未来收益是正确的（预测目标）
_DEFAULT_LABEL = "Ref($close, -1) / $close - 1"

# AutoML 因子表达式：AutoML(method,task_id)，回测时加载 bundle 重建特征预测
_AUTOML_EXPR_RE = re.compile(r"^AutoML\((lightgbm|linear),\s*([\d,\s]+)\)$", re.IGNORECASE)


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


def _resolve_task_id_from_factor_ids(method: str, factor_ids: list):
    """旧格式 AutoML(method, fid1, fid2, ...) 反查 task_id。

    旧表达式里数字是基础因子 id，无法直接得到 task_id；通过表达式精确
    匹配 factor 表的 source_task_id 字段获取。查不到时返回 None。
    """
    import sqlite3
    expr = f"AutoML({method},{','.join(map(str, factor_ids))})"
    try:
        with sqlite3.connect(settings.db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT source_task_id FROM factor WHERE expression = ? AND source_task_id IS NOT NULL",
                (expr,),
            )
            row = cur.fetchone()
        return str(row[0]) if row else None
    except Exception as e:
        logger.warning("反查 AutoML task_id 失败 expr=%s: %s", expr, e)
        return None


def _load_automl_factor(method: str, ids: list, start: str, end: str,
                        universe: str = None) -> pd.DataFrame:
    """加载 AutoML 组合因子：解析 bundle，重建基础特征后用模型预测。

    支持两种表达式格式：
      - 新格式 AutoML(method, task_id)：单 id 视为 task_id
      - 旧格式 AutoML(method, fid1, fid2, ...)：多 id 视为基础因子 id 列表，
        通过表达式反查 factor 表的 source_task_id 得到 task_id

    bundle 缺失时抛 FileNotFoundError，由上游返回友好错误而非 500。
    """
    from app.services.mining.automl import load_automl_bundle, predict_with_automl_model

    # 解析 task_id
    if len(ids) == 1:
        task_id = str(ids[0])
    else:
        task_id = _resolve_task_id_from_factor_ids(method, ids)
        if task_id is None:
            raise FileNotFoundError(
                f"AutoML 表达式 AutoML({method},{','.join(map(str, ids))}) "
                f"无法解析 task_id（factor 表无匹配记录）"
            )

    try:
        bundle = load_automl_bundle(task_id)
    except FileNotFoundError as e:
        raise FileNotFoundError(
            f"AutoML 模型 bundle 丢失 (task_id={task_id})。"
            f"请重新训练该任务以重建模型，或停用对应因子。原始错误: {e}"
        )

    feature_names = bundle.get("feature_names") or []
    factor_exprs = bundle.get("factor_expressions") or {}
    if not feature_names:
        raise ValueError(f"AutoML bundle {task_id} 缺少 feature_names")

    # 递归加载各基础因子（均为普通 qlib 表达式，不会再次进入 AutoML 分支）
    frames = []
    for name in feature_names:
        expr = factor_exprs.get(name)
        if not expr:
            raise ValueError(f"AutoML bundle {task_id} 缺少特征 {name} 的表达式")
        fdf = load_factor_values(expr, start, end, universe=universe)
        frames.append(fdf.rename(columns={"factor": name}))
    X_df = pd.concat(frames, axis=1)

    # 与训练时一致的截面标准化
    for n in feature_names:
        X_df[n] = X_df.groupby(level="datetime")[n].transform(
            lambda x: (x - x.mean()) / (x.std(ddof=0) + 1e-8)
        )
    X_df = X_df.dropna(subset=feature_names)
    if X_df.empty:
        raise ValueError(f"AutoML 因子 {task_id} 重建特征为空")

    preds = predict_with_automl_model(task_id, X_df)
    return pd.DataFrame({"factor": preds}, index=X_df.index)


def load_factor_values(
    factor_expr: str,
    start: str,
    end: str,
    universe: str = None,
    neutralize: str = None,
) -> pd.DataFrame:
    """加载因子值，返回 MultiIndex (datetime, instrument) DataFrame。

    支持特殊表达式 AutoML(method,task_id)：加载已训练模型 bundle 重建特征并预测。

    Args:
        neutralize: 中性化方式
            None: 不做中性化（默认）
            "market_cap": 市值中性化
            "industry": 行业+市值中性化
    """
    # AutoML 组合因子：拦截后由模型预测生成
    automl_match = _AUTOML_EXPR_RE.match(factor_expr or "")
    if automl_match:
        method = automl_match.group(1).lower()
        ids = [int(x.strip()) for x in automl_match.group(2).split(",") if x.strip()]
        logger.info("加载 AutoML 因子: method=%s ids=%s", method, ids)
        df = _load_automl_factor(method, ids, start, end, universe=universe)
    else:
        init_qlib()
        from qlib.data import D
        # 防御性 look-ahead 检查：禁止负数 Ref（未来数据），即便表达式绕过创建时校验
        from app.services.factor.expression import check_lookahead
        check_lookahead(factor_expr)
        market = universe or settings.quant.get("universe", "csi300")
        instruments = _load_instruments(market)
        df = D.features(instruments, [factor_expr], start_time=start, end_time=end, freq="day")
        if df is None or df.empty:
            raise ValueError(f"因子 {factor_expr} 在 {start}~{end} 无数据")
        df = df.rename(columns={df.columns[0]: "factor"})

    # 因子中性化
    if neutralize in ("market_cap", "industry"):
        from app.services.factor.neutralize import market_cap_neutralize, industry_neutralize
        if neutralize == "market_cap":
            df = market_cap_neutralize(df, factor_col="factor")
        else:
            df = industry_neutralize(df, factor_col="factor")
        # 用中性化后的值替换原始因子值，保持 "factor" 列名不变
        df["factor"] = df["factor_neutralized"]
        df = df.drop(columns=["factor_neutralized"])

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
        lambda g: g["factor"].corr(g["label"]) if len(g) >= 2 else np.nan,
        include_groups=False,
    ).dropna()
    daily_rank_ic = merged.groupby(level="datetime").apply(
        lambda g: g["factor"].corr(g["label"], method="spearman") if len(g) >= 2 else np.nan,
        include_groups=False,
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
    """计算 IC 衰减：因子与未来 1~max_lag 日收益的 IC 序列。

    优化：一次查询 $close 后本地 shift 计算各 lag 前向收益，避免 N 次 qlib IO。
    """
    init_qlib()
    from qlib.data import D
    market = settings.quant.get("universe", "csi300")
    instruments = _load_instruments(market)
    start = factor_df.index.get_level_values("datetime").min()
    end = factor_df.index.get_level_values("datetime").max()

    # 一次查询 $close，本地算各 lag 收益
    try:
        close_df = D.features(instruments, ["$close"], start_time=str(start.date()), end_time=str(end.date()), freq="day")
        if close_df is None or close_df.empty:
            return {}
        close_df = close_df.rename(columns={"$close": "close"})
    except Exception as e:
        logger.debug("decay 查询 $close 失败: %s", e)
        return {}

    decay = {}
    for lag in range(1, max_lag + 1):
        try:
            # 前向 lag 日收益：close.shift(-lag) / close - 1（按 instrument 分组）
            close_df["fwd_ret"] = close_df.groupby(level="instrument")["close"].shift(-lag) / close_df["close"] - 1
            m = factor_df.join(close_df[["fwd_ret"]].rename(columns={"fwd_ret": "label"}), how="inner").dropna()
            if m.empty:
                continue
            ic = m.groupby(level="datetime").apply(
                lambda g: g["factor"].corr(g["label"]) if len(g) > 5 else np.nan,
                include_groups=False,
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



def compute_quantile_returns(
    factor_df: pd.DataFrame,
    return_df: pd.DataFrame,
    n_groups: int = 5,
    factor_col: str = "factor",
    return_col: str = "label",
) -> dict:
    """计算因子分组收益（分层回测）。

    每个截面按因子值分 n_groups 组，统计各组日均收益、净值曲线、多空收益
    及组间收益单调性。

    Args:
        factor_df: MultiIndex(datetime, instrument) DataFrame，含 factor_col
        return_df: MultiIndex(datetime, instrument) DataFrame，含 return_col
            （与 load_label 输出对齐，列名 label）
        n_groups: 分组数
        factor_col: 因子值列名
        return_col: 收益列名（默认 label，与 load_label 一致）

    Returns:
        {group_returns, group_nav, group_stats, long_short_returns,
         long_short_nav, monotonicity_score, dates}
    """
    # 合并因子与收益
    merged = factor_df.join(return_df, how="inner")
    if merged.empty:
        return {"error": "无有效数据"}

    dates = sorted(merged.index.get_level_values("datetime").unique())

    group_daily_returns = {i: [] for i in range(1, n_groups + 1)}
    group_dates = {i: [] for i in range(1, n_groups + 1)}

    for dt in dates:
        day_data = merged.xs(dt, level="datetime").dropna(subset=[factor_col, return_col])
        if len(day_data) < n_groups:
            continue
        # 按因子值分位分组：qcut 可能因重复值失败，降级用 rank
        try:
            day_data = day_data.copy()
            day_data["group"] = pd.qcut(day_data[factor_col], n_groups, labels=False, duplicates="drop") + 1
        except Exception:
            ranks = day_data[factor_col].rank(method="first")
            day_data = day_data.copy()
            day_data["group"] = pd.cut(ranks, n_groups, labels=False) + 1
        day_data = day_data.dropna(subset=["group"])
        if day_data["group"].nunique() < 2:
            continue

        for g in range(1, n_groups + 1):
            g_data = day_data[day_data["group"] == g]
            if len(g_data) > 0:
                g_return = float(g_data[return_col].mean())
                group_daily_returns[g].append(g_return)
                group_dates[g].append(dt)

    # 各组净值曲线与统计
    group_nav = {}
    group_stats = []
    for g in range(1, n_groups + 1):
        returns = group_daily_returns[g]
        nav = np.cumprod(1 + np.array(returns)) if returns else np.array([1.0])
        group_nav[g] = nav.tolist()
        mean_ret = float(np.mean(returns)) if returns else 0.0
        std_ret = float(np.std(returns)) if returns else 1.0
        sharpe = mean_ret / std_ret * np.sqrt(252) if std_ret > 0 else 0.0
        group_stats.append({
            "group": g,
            "mean_daily_return": mean_ret,
            "annualized_return": float(mean_ret * 252),
            "sharpe": float(sharpe),
            "days": len(returns),
        })

    # 多空收益（最高组 - 最低组），按日期对齐
    long_series = pd.Series(group_daily_returns[n_groups], index=group_dates[n_groups], name="long")
    short_series = pd.Series(group_daily_returns[1], index=group_dates[1], name="short")
    aligned = pd.concat([long_series, short_series], axis=1).dropna()
    long_short = (aligned["long"] - aligned["short"]).tolist()
    long_short_nav = (np.cumprod(1 + np.array(long_short)) if long_short else np.array([1.0])).tolist()

    # 单调性：组号与组均收益的 Spearman 相关（pandas 实现，无需 scipy）
    mean_returns = [group_stats[g - 1]["mean_daily_return"] for g in range(1, n_groups + 1)]
    mono_corr = float(pd.Series(mean_returns).corr(pd.Series(range(1, n_groups + 1)), method="spearman"))
    if np.isnan(mono_corr):
        mono_corr = 0.0

    return {
        "group_returns": {str(k): v for k, v in group_daily_returns.items()},
        "group_nav": {str(k): v for k, v in group_nav.items()},
        "group_stats": group_stats,
        "long_short_returns": long_short,
        "long_short_nav": long_short_nav,
        "monotonicity_score": mono_corr,
        "n_groups": n_groups,
        "dates": [str(d.date()) for d in aligned.index.tolist()],
    }
