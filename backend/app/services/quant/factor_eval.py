"""因子评价封装：基于 alphalens-reloaded 计算 IC/RankIC/ICIR/分层收益/换手/衰减。

设计：数据通过 qlib D.features 加载，内部使用 alphalens-reloaded 计算指标，
保留原有接口签名不变以实现外部调用无感知。
"""
import re
import logging
from functools import lru_cache
import numpy as np
import pandas as pd
import alphalens
from app.services.quant.qlib_init import init_qlib
from app.core.config import settings

logger = logging.getLogger(__name__)

# 前向收益标签：t 日收盘到 t+1 日收盘的收益（与回测引擎 shift(-1) 口径一致）
# 注意：Ref 负数=未来，label 用未来收益是正确的（预测目标）
_DEFAULT_LABEL = "Ref($close, -1) / $close - 1"

# AutoML 因子表达式：AutoML(method,task_id)，回测时加载 bundle 重建特征预测
# method 支持 lightgbm/linear（单模型）与 walk_forward（滚动重训，直接读持久化打分）
_AUTOML_EXPR_RE = re.compile(r"^AutoML\((lightgbm|linear|walk_forward),\s*([\d,\s]+)\)$", re.IGNORECASE)


@lru_cache(maxsize=8)
def _load_instruments_cached(market: str) -> tuple:
    """缓存的股票池加载（按 market 缓存），返回 tuple 满足 lru_cache 要求。

    同一进程内多次调用只会触发一次 qlib D.list_instruments 查询，
    避免 Alpha158 批量评价等场景里 158 次重复 IO。
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
    return tuple(codes)


def _load_instruments(market: str) -> list:
    """加载股票池代码列表，默认过滤北交所（bj 开头）股票。

    通过 qlib D.list_instruments 获取成分股列表，
    根据 settings.quant.include_bj 控制是否保留北交所股票。
    内部走 _load_instruments_cached 实现进程级缓存。
    """
    return list(_load_instruments_cached(market))


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

    # Walk-Forward bundle：直接读持久化的 OOS 打分序列（回测免重训）
    if bundle.get("method") == "walk_forward":
        score_df = bundle.get("score_df")
        if score_df is None or score_df.empty:
            raise ValueError(f"AutoML walk_forward bundle {task_id} 缺少打分序列")
        sub = score_df[(score_df.index.get_level_values("datetime") >= pd.Timestamp(start)) &
                       (score_df.index.get_level_values("datetime") <= pd.Timestamp(end))]
        return pd.DataFrame({"factor": sub["factor"]}, index=sub.index)

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
        return df
    else:
        # 文本因子等需要外部数据的算子 qlib 未注册，提前给出明确错误而非 AttributeError
        _unsupported = ("TextSentiment", "NewsSentiment")
        if any(op in (factor_expr or "") for op in _unsupported):
            raise ValueError(
                f"因子表达式含未注册算子（{factor_expr}），文本因子需重新挖掘以预计算值，不支持实时计算"
            )
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


def _to_alphalens_factor_data(factor_df: pd.DataFrame, label_df: pd.DataFrame,
                              period_name: str = "1D") -> pd.DataFrame | None:
    """将 (factor_df, label_df) 转换为 alphalens 兼容格式。

    factor_df: MultiIndex (datetime, instrument), 列 "factor"
    label_df: MultiIndex (datetime, instrument), 列 "label"（前向收益）
    period_name: 前向收益周期列名，alphalens 约定如 "1D"/"5D"
    Returns: MultiIndex (date, asset), 列 ["factor", period_name]，或 None
    """
    merged = factor_df.join(label_df, how="inner").dropna()
    if merged.empty:
        return None
    factor_data = merged.rename(columns={"label": period_name})
    # qlib D.features 返回 (instrument, datetime)，alphalens 要求 (date, asset)，
    # 必须换序而非仅改名，否则 date/asset 语义错位（level 0 实际是股票代码）。
    # 按 level 0 类型自适应：已是 DatetimeIndex（如测试数据）则保持原序。
    if not isinstance(factor_data.index.levels[0], pd.DatetimeIndex):
        factor_data = factor_data.swaplevel(0, 1).sort_index()
    factor_data.index = factor_data.index.set_names(["date", "asset"])
    # alphalens 需要 date level 的 .freq（pandas 2.x 的 MultiIndex 不再保留 freq）
    # 直接写底层 _freq 绕过频率一致性校验（节假日跳空时严格校验会失败）
    date_level = pd.DatetimeIndex(factor_data.index.levels[0])
    try:
        date_level._data._freq = pd.offsets.BDay()
    except Exception:
        pass
    factor_data.index = factor_data.index.set_levels([date_level, factor_data.index.levels[1]])
    return factor_data


def compute_ic(factor_df: pd.DataFrame, label_df: pd.DataFrame) -> dict:
    """计算 IC/RankIC/ICIR/IR。

    factor_df, label_df: MultiIndex (datetime, instrument)，列名 factor/label

    注意：alphalens-reloaded 的 factor_information_coefficient 计算的是
    Spearman Rank IC（即 RankIC），Pearson IC 在此手动计算。
    """
    factor_data = _to_alphalens_factor_data(factor_df, label_df)
    if factor_data is None or factor_data.empty:
        return {"ic": None, "rank_ic": None, "icir": None, "ir": None, "n_days": 0}

    # alphalens 的 factor_information_coefficient 计算的是 Spearman Rank IC
    rank_ic_df = alphalens.performance.factor_information_coefficient(factor_data)
    daily_rank_ic = rank_ic_df["1D"].dropna()

    # Pearson IC 手动计算
    daily_ic = factor_data.groupby(level="date").apply(
        lambda g: g["factor"].corr(g["1D"]) if len(g) >= 2 else np.nan,
        include_groups=False,
    ).dropna()

    ic_mean = float(daily_ic.mean()) if len(daily_ic) else None
    ic_std = float(daily_ic.std(ddof=1)) if len(daily_ic) > 1 else None
    rank_ic_mean = float(daily_rank_ic.mean()) if len(daily_rank_ic) else None
    rank_ic_std = float(daily_rank_ic.std(ddof=1)) if len(daily_rank_ic) > 1 else None

    icir = float(ic_mean / ic_std) if ic_mean is not None and ic_std else None
    ir = float(rank_ic_mean / rank_ic_std) if rank_ic_mean is not None and rank_ic_std else None

    return {
        "ic": round(ic_mean, 4) if ic_mean is not None else None,
        "rank_ic": round(rank_ic_mean, 4) if rank_ic_mean is not None else None,
        "icir": round(icir, 4) if icir is not None else None,
        "ir": round(ir, 4) if ir is not None else None,
        "n_days": int(len(daily_ic)),
    }


def _topk_holdings_mask(wide: pd.DataFrame, k: int) -> pd.DataFrame:
    """宽表（date × instrument）每日取 topk 的持仓布尔掩码。

    rank(method="first") 与旧实现 set(nlargest(k)) 的并列股选择口径一致：
    并列时按首次出现顺序截取前 k 只。
    """
    ranks = wide.rank(axis=1, method="first", ascending=False)
    return ranks <= k


def compute_turnover(factor_df: pd.DataFrame) -> float:
    """计算因子换手率：每日持仓变动均值（按 topk 截面排名近似）。"""
    topk = settings.quant.get("topk", 50)
    # 宽表（date × instrument）→ 布尔 nlargest 掩码，逐日向量化取 topk 与重合度
    wide = factor_df["factor"].unstack(level="instrument").sort_index()
    if wide.empty:
        return None
    holdings = _topk_holdings_mask(wide, topk)

    prev = holdings.shift(1)
    overlap = (holdings & prev.fillna(False)).sum(axis=1)
    prev_cnt = prev.sum(axis=1)
    safe_cnt = prev_cnt.replace(0, np.nan)
    turnovers = (1 - overlap / safe_cnt).dropna()
    return round(float(turnovers.mean()), 4) if len(turnovers) else None


def compute_decay(factor_df: pd.DataFrame, label_df: pd.DataFrame, max_lag: int = 10,
                  preloaded_close_df: pd.DataFrame = None) -> dict:
    """计算 IC 衰减：因子与未来 1~max_lag 日收益的 IC 序列。

    使用 alphalens 多周期前向收益功能，一次查询 $close 后本地计算各 lag 的 IC。
    优化：调用方可传入 preloaded_close_df 跳过重复 IO（批量评价场景）。
    """
    start = factor_df.index.get_level_values("datetime").min()
    end = factor_df.index.get_level_values("datetime").max()

    if preloaded_close_df is not None and not preloaded_close_df.empty:
        try:
            close_df = preloaded_close_df.rename(columns={"$close": "close"}).copy()
        except Exception as e:
            logger.debug("decay 使用预加载 close 失败，回退查询: %s", e)
            preloaded_close_df = None

    if preloaded_close_df is None:
        init_qlib()
        from qlib.data import D
        market = settings.quant.get("universe", "csi300")
        instruments = _load_instruments(market)

        try:
            close_df = D.features(instruments, ["$close"],
                                  start_time=str(start.date()), end_time=str(end.date()), freq="day")
            if close_df is None or close_df.empty:
                return {}
            close_df = close_df.rename(columns={"$close": "close"})
        except Exception as e:
            logger.debug("decay 查询 $close 失败: %s", e)
            return {}

    # 转换为 alphalens 格式：wide prices (date × instrument)
    close_wide = close_df["close"].unstack(level="instrument")
    # 确保日期索引有序
    close_wide = close_wide.sort_index()

    # 提取因子 Series
    factor_s = factor_df["factor"].copy()
    factor_s.index = factor_s.index.set_names(["date", "asset"])

    # 使用 alphalens 多周期前向收益，一次计算所有 lag
    periods = tuple(range(1, max_lag + 1))
    try:
        alphalens_factor_data = alphalens.utils.get_clean_factor_and_forward_returns(
            factor=factor_s,
            prices=close_wide,
            periods=periods,
            quantiles=5,
            max_loss=0.35,
        )
    except Exception as e:
        logger.debug("decay alphalens 前向收益计算失败: %s", e)
        return {}

    # 计算各周期 IC
    ic_df = alphalens.performance.factor_information_coefficient(alphalens_factor_data)

    decay = {}
    for lag in range(1, max_lag + 1):
        period_col = f"{lag}D"
        if period_col in ic_df.columns:
            ic_series = ic_df[period_col].dropna()
            decay[lag] = round(float(ic_series.mean()), 4) if len(ic_series) else None
        else:
            decay[lag] = None
    return decay


def evaluate_factor(factor_expr: str, start: str, end: str, universe: str = None,
                    horizon: int = None, preloaded_label_df: pd.DataFrame = None,
                    preloaded_close_df: pd.DataFrame = None,
                    horizons: list = None) -> dict:
    """完整因子评价：IC/RankIC/ICIR/换手/衰减 + 多周期 IC。

    Args:
        horizon: 主预测周期（标签前向收益天数）。默认从 config 读取。
        preloaded_label_df: 预加载的标签 DataFrame，避免批量场景下重复 IO。
        preloaded_close_df: 预加载的 $close DataFrame，传入 compute_decay 避免重复 IO。
        horizons: 额外评价的周期列表（如 [1, 10, 20]），计算 ic_by_horizon。
    """
    if horizon is None:
        horizon = settings.mining.get("llm", {}).get("eval_horizon", 5)
    label_expr = f"Ref($close, -{horizon}) / $close - 1"
    factor_df = load_factor_values(factor_expr, start, end, universe)
    if preloaded_label_df is not None:
        label_df = preloaded_label_df
    else:
        label_df = load_label(start, end, label_expr=label_expr, universe=universe)
    ic_metrics = compute_ic(factor_df, label_df)
    turnover = compute_turnover(factor_df)
    decay = compute_decay(factor_df, label_df, preloaded_close_df=preloaded_close_df)

    result = {
        **ic_metrics,
        "turnover": turnover,
        "decay": decay,
        "eval_start": start,
        "eval_end": end,
        "factor_expr": factor_expr,
        "horizon": horizon,
    }

    # 多周期评价：对额外 horizon 计算 IC，输出 ic_by_horizon + 稳定性
    if horizons:
        ic_by_horizon = {}
        signs = []
        for h in horizons:
            if h == horizon:
                ic_by_horizon[str(h)] = ic_metrics.get("ic")
                if ic_metrics.get("ic") is not None:
                    signs.append(1 if ic_metrics["ic"] > 0 else -1)
                continue
            h_label = f"Ref($close, -{h}) / $close - 1"
            h_df = load_label(start, end, label_expr=h_label, universe=universe)
            h_ic = compute_ic(factor_df, h_df).get("ic")
            ic_by_horizon[str(h)] = h_ic
            if h_ic is not None:
                signs.append(1 if h_ic > 0 else -1)
        # 稳定性：各周期 IC 同号比例
        stability = abs(sum(signs)) / len(signs) if signs else None
        ic_by_horizon[str(horizon)] = ic_by_horizon.get(str(horizon), ic_metrics.get("ic"))
        result["ic_by_horizon"] = ic_by_horizon
        result["multi_horizon_stability"] = round(stability, 4) if stability is not None else None

    return result


def compute_quantile_returns(
    factor_df: pd.DataFrame,
    return_df: pd.DataFrame,
    n_groups: int = 5,
    factor_col: str = "factor",
    return_col: str = "label",
) -> dict:
    """计算因子分组收益（分层回测）。

    使用 alphalens 进行分层收益计算：
    1. 从前向收益重构 mock prices（收益尺度不变，仅用作 alphalens 输入）
    2. 用 alphalens 计算各分位组日均收益
    3. 输出各组净值曲线、多空收益及组间收益单调性

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

    # 从前向收益重构 mock prices（alphalens 需要 prices 而非直接的前向收益）
    # 构造方法：每个 instrument 独立，price_t = 100 * cumprod(1 + return_{<t})
    ret_wide = merged[return_col].unstack(level="instrument")
    # 极端值裁剪，避免 mock prices 爆炸
    ret_clipped = ret_wide.clip(-0.5, 1.0)
    price_wide = 100.0 * (1 + ret_clipped).cumprod()
    price_wide = price_wide.ffill().bfill()

    # 因子 Series
    factor_s = merged[factor_col].copy()
    factor_s.index = factor_s.index.set_names(["date", "asset"])

    try:
        # 使用 alphalens 准备数据（含分位分配）
        factor_data = alphalens.utils.get_clean_factor_and_forward_returns(
            factor=factor_s,
            prices=price_wide,
            quantiles=n_groups,
            periods=(1,),
            max_loss=0.35,
        )
    except Exception as e:
        logger.debug("quantile_returns alphalens 准备失败: %s", e)
        return {"error": f"alphalens 数据处理失败: {e}"}

    if factor_data is None or factor_data.empty:
        return {"error": "alphalens 处理后无有效数据"}

    # 获取各分位组日均收益（by_date=True 得到每日每组均值）
    mean_ret, _ = alphalens.performance.mean_return_by_quantile(factor_data, by_date=True)

    if mean_ret is None or mean_ret.empty:
        return {"error": "无有效分组收益数据"}

    # 转换 mean_ret 格式：MultiIndex(date, factor_quantile) → {group: [returns]}
    # mean_ret 的列是周期名（如 "1D"），行是 (date, factor_quantile)
    period_col = mean_ret.columns[0]  # "1D"

    group_daily_returns = {}
    group_dates = {}
    for g in range(1, n_groups + 1):
        try:
            g_data = mean_ret.xs(g, level="factor_quantile")[period_col].dropna()
            group_daily_returns[g] = g_data.values.tolist()
            group_dates[g] = g_data.index.tolist()
        except KeyError:
            group_daily_returns[g] = []
            group_dates[g] = []

    # 各组净值曲线与统计
    group_nav = {}
    group_stats = []
    for g in range(1, n_groups + 1):
        returns = group_daily_returns[g]
        nav = np.cumprod(1 + np.array(returns)) if returns else np.array([1.0])
        group_nav[g] = nav.tolist()
        mean_ret_val = float(np.mean(returns)) if returns else 0.0
        std_ret = float(np.std(returns, ddof=1)) if returns and len(returns) > 1 else 1.0
        sharpe = mean_ret_val / std_ret * np.sqrt(252) if std_ret > 0 else 0.0
        group_stats.append({
            "group": g,
            "mean_daily_return": mean_ret_val,
            "annualized_return": float(mean_ret_val * 252),
            "sharpe": float(sharpe),
            "days": len(returns),
        })

    # 多空收益（最高组 - 最低组），按日期对齐
    long_series = pd.Series(group_daily_returns.get(n_groups, []),
                            index=group_dates.get(n_groups, []), name="long")
    short_series = pd.Series(group_daily_returns.get(1, []),
                             index=group_dates.get(1, []), name="short")
    aligned = pd.concat([long_series, short_series], axis=1).dropna()
    long_short = (aligned["long"] - aligned["short"]).tolist() if not aligned.empty else []
    long_short_nav = (np.cumprod(1 + np.array(long_short)) if long_short else np.array([1.0])).tolist()

    # 单调性：组号与组均收益的 Spearman 相关
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
        "dates": [str(d.date()) if hasattr(d, 'date') else str(d) for d in aligned.index.tolist()],
    }


# ==================== 因子深度分析 ====================
# 以下函数为因子深度分析（deep-analysis）提供 IC 分布/时序/显著性、
# horizon 同步调仓分层净值、换手率曲线及聚合入口，供 factor_ext.py 调用。


def _daily_rank_ic_series(factor_df: pd.DataFrame, label_df: pd.DataFrame) -> pd.Series:
    """每日截面 Spearman IC 序列（私有复用）。

    使用 alphalens 的 factor_information_coefficient（Spearman Rank IC）计算。
    """
    factor_data = _to_alphalens_factor_data(factor_df, label_df)
    if factor_data is None or factor_data.empty:
        return pd.Series(dtype=float)
    rank_ic_df = alphalens.performance.factor_information_coefficient(factor_data)
    return rank_ic_df["1D"].dropna()


def compute_ic_distribution(factor_df: pd.DataFrame, label_df: pd.DataFrame, n_bins: int = 20) -> dict:
    """IC 分布：每日截面 Spearman IC 序列的分箱统计。

    Returns: {bins, counts, stats: {mean, std, skew, positive_ratio}}
    """
    from scipy import stats

    daily_ic = _daily_rank_ic_series(factor_df, label_df)
    if daily_ic.empty:
        return {
            "bins": [],
            "counts": [],
            "stats": {"mean": None, "std": None, "skew": None, "positive_ratio": None},
        }

    values = daily_ic.values
    counts, bins = np.histogram(values, bins=n_bins)
    mean = float(daily_ic.mean())
    std = float(daily_ic.std()) if len(daily_ic) > 1 else None
    skew = float(stats.skew(values)) if len(daily_ic) >= 3 else None
    positive_ratio = float((daily_ic > 0).mean())

    return {
        "bins": [float(b) for b in bins],
        "counts": [int(c) for c in counts],
        "stats": {
            "mean": mean,
            "std": std,
            "skew": skew,
            "positive_ratio": positive_ratio,
        },
    }


def compute_ic_timeseries(factor_df: pd.DataFrame, label_df: pd.DataFrame, window: int = 60) -> dict:
    """IC 时序：每日截面 IC + 滚动均线。

    Returns: {dates, ic_series, ic_ma}
    """
    daily_ic = _daily_rank_ic_series(factor_df, label_df)
    if daily_ic.empty:
        return {"dates": [], "ic_series": [], "ic_ma": []}

    ic_ma = daily_ic.rolling(window).mean()
    return {
        "dates": [str(d.date()) for d in daily_ic.index],
        "ic_series": [float(v) for v in daily_ic.values],
        "ic_ma": [None if np.isnan(v) else float(v) for v in ic_ma.values],
    }


def compute_ic_significance(ic_series: pd.Series, lags: int = None) -> dict:
    """IC 统计显著性：Newey-West 校正 t 检验（处理重叠标签的自相关）。

    Returns: {t_stat, p_value, significant, n_days, note}
    """
    from app.services.quant.factor_validator import newey_west_t

    s = pd.Series(ic_series).dropna()
    n = int(len(s))
    note = "Newey-West 校正 t 检验（lags=%s），处理重叠标签自相关" % (
        lags if lags is not None else "auto"
    )
    if n < 2:
        return {"t_stat": None, "p_value": None, "significant": False, "n_days": n, "note": note}

    t_stat, p_value = newey_west_t(s, lags=lags)
    if t_stat is None or np.isnan(t_stat) or np.isnan(p_value):
        return {"t_stat": None, "p_value": None, "significant": False, "n_days": n, "note": note}
    return {
        "t_stat": float(t_stat),
        "p_value": float(p_value),
        "significant": bool(p_value < 0.05),
        "n_days": n,
        "note": note,
    }


def compute_quantile_nav_by_horizon(
    factor_df: pd.DataFrame,
    prices_df: pd.DataFrame,
    n_groups: int = 5,
    horizon: int = 5,
) -> dict:
    """按 horizon 周期调仓的分层累计净值（矩阵化实现）。

    - 每 horizon 个交易日调仓一次
    - 调仓日按因子值分 n_groups 组，等权持仓
    - 持有 horizon 天后重新调仓

    向量化策略：构造 (日期 × 股票) 分组矩阵 G，单次广播计算各组日收益，
    消除逐日 × 逐组的 Python 循环（O(days×stocks) 落到 numpy）。

    Returns: {dates, quantile_nav, long_short_nav, annualized_returns, long_short_annual_return, monotonicity}
    """
    empty = {
        "dates": [],
        "quantile_nav": {f"Q{g}": [] for g in range(1, n_groups + 1)},
        "long_short_nav": [],
        "annualized_returns": {f"Q{g}": None for g in range(1, n_groups + 1)},
        "long_short_annual_return": None,
        "monotonicity": 0.0,
    }

    if factor_df is None or factor_df.empty or prices_df is None or prices_df.empty:
        return empty

    dates = sorted(factor_df.index.get_level_values("datetime").unique())
    dates = pd.DatetimeIndex(dates)
    if len(dates) == 0:
        return empty
    n_days = len(dates)

    # 宽表对齐：日期 × 股票，因子值与收益矩阵（NaN 表示缺失）
    factor_wide = factor_df["factor"].unstack(level="instrument")
    factor_wide = factor_wide.reindex(dates)
    insts = factor_wide.columns
    F = factor_wide.to_numpy(dtype=float)

    ret_wide = prices_df.pct_change()
    ret_date_in = dates.isin(ret_wide.index)
    R = ret_wide.reindex(dates).reindex(columns=insts).to_numpy(dtype=float)

    rb_positions = list(range(0, n_days, horizon))

    def _group_vec(day_factor: np.ndarray, idx_valid: np.ndarray) -> np.ndarray | None:
        """调仓日分组向量：长度 n 的数组，值 1..n_groups，无效位置填 NaN。"""
        if idx_valid.sum() < n_groups:
            return None
        vals = pd.Series(day_factor[idx_valid]).astype(float)
        try:
            groups = pd.qcut(vals, n_groups, labels=False, duplicates="drop")
        except Exception:
            ranks = vals.rank(method="first")
            groups = pd.cut(ranks, n_groups, labels=False)
        groups = (groups + 1).dropna()
        if groups.nunique() < 2:
            return None
        vec = np.full(len(insts), np.nan)
        vec[idx_valid] = groups.reindex(vals.index).to_numpy(dtype=float)
        return vec

    # 构造 (日期 × 股票) 分组矩阵：行 p 为持仓期，值 1..n_groups，无效行为 0
    G = np.zeros((n_days, len(insts)), dtype=np.int16)
    for q in rb_positions:
        day_f = F[q]
        day_na = np.isnan(day_f)
        gv = _group_vec(day_f.copy(), ~day_na)
        if gv is None:
            continue
        nxt = min(q + horizon, n_days)
        G[q + 1:nxt + 1] = np.nan_to_num(gv, nan=0).astype(np.int16)

    valid_ret = ~np.isnan(R)
    group_daily = {}
    for g in range(1, n_groups + 1):
        mask = (G == g) & valid_ret
        num = np.where(mask, R, 0.0).sum(axis=1)
        den = mask.sum(axis=1)
        safe_den = np.where(den > 0, den, 1)
        group_daily[g] = np.where(den > 0, num / safe_den, 0.0)

    # 有效持仓日：属于某分组且当天确实有行情
    active = G.any(axis=1) & ret_date_in
    active_pos = np.where(active)[0]
    nav_dates = [dates[p] for p in active_pos]

    quantile_nav = {}
    annualized_returns = {}
    mean_returns = []
    for g in range(1, n_groups + 1):
        seq = group_daily[g][active_pos]
        nav = np.cumprod(1.0 + seq) if len(seq) else np.array([], dtype=float)
        quantile_nav[f"Q{g}"] = nav.tolist()
        mr = float(np.mean(seq)) if len(seq) else 0.0
        annualized_returns[f"Q{g}"] = float(mr * 252)
        mean_returns.append(mr)

    ls_seq = group_daily[n_groups][active_pos] - group_daily[1][active_pos]
    long_short_nav = (np.cumprod(1.0 + ls_seq) if len(ls_seq) else np.array([], dtype=float)).tolist()
    ls_annual = float(np.mean(ls_seq) * 252) if len(ls_seq) else 0.0

    mono = float(pd.Series(mean_returns).corr(pd.Series(range(1, n_groups + 1)), method="spearman"))
    if np.isnan(mono):
        mono = 0.0

    return {
        "dates": [str(d.date()) for d in nav_dates],
        "quantile_nav": {f"Q{g}": [float(v) for v in quantile_nav[f"Q{g}"]] for g in range(1, n_groups + 1)},
        "long_short_nav": [float(v) for v in long_short_nav],
        "annualized_returns": annualized_returns,
        "long_short_annual_return": ls_annual,
        "monotonicity": mono,
    }


def compute_turnover_curve(factor_df: pd.DataFrame, n_groups: int = 5, horizon: int = 5) -> dict:
    """分组换手率时序（多头组：因子值最高组，矩阵化实现）。

    - 每 horizon 日调仓
    - turnover = 1 - |新∩旧| / |旧|

    向量化：只在调仓日子表上一次性 rank 选股，逐对广播计算重合度，
    消除逐 T 的 nlargest 循环。

    Returns: {dates, turnover_series, avg_turnover, annual_turnover}
    """
    empty = {"dates": [], "turnover_series": [], "avg_turnover": None, "annual_turnover": None}
    if factor_df is None or factor_df.empty:
        return empty

    dates = sorted(factor_df.index.get_level_values("datetime").unique())
    dates = pd.DatetimeIndex(dates)
    if len(dates) == 0:
        return empty

    rebalance_dates = dates[::horizon]
    # 调仓日子表：宽表（date × instrument）→ rank 掩码（并列选前 top_n）
    wide = factor_df["factor"].unstack(level="instrument")
    rb_wide = wide.reindex(rebalance_dates)
    valid_cnt = rb_wide.notna().sum(axis=1)
    masks = {}
    for rb in rebalance_dates:
        if valid_cnt.get(rb, 0) < n_groups:
            continue
        top_n = max(1, int(valid_cnt[rb]) // n_groups)
        row = rb_wide.loc[rb].dropna()
        masks[rb] = set(row.nlargest(top_n).index.tolist())

    turnover_series = []
    turnover_dates = []
    prev = None
    for rb in rebalance_dates:
        holdings = masks.get(rb)
        if holdings is None:
            continue
        if prev is not None and len(prev) > 0:
            overlap = len(holdings & prev)
            turnover_series.append(float(1.0 - overlap / len(prev)))
            turnover_dates.append(rb)
        prev = holdings

    avg_turnover = float(np.mean(turnover_series)) if turnover_series else None
    annual_turnover = float(avg_turnover * (252.0 / horizon)) if avg_turnover is not None else None
    return {
        "dates": [str(d.date()) for d in turnover_dates],
        "turnover_series": turnover_series,
        "avg_turnover": avg_turnover,
        "annual_turnover": annual_turnover,
    }


def deep_analyze_factor(
    factor_expr: str,
    start: str,
    end: str,
    universe: str = None,
    horizon: int = 5,
    n_groups: int = 5,
    ic_window: int = 60,
) -> dict:
    """因子深度分析聚合：一次性返回所有分析数据。

    内部复用 load_factor_values/load_label + 上述 5 个函数 + compute_decay。
    label 使用 horizon 周期前向收益（Ref($close,-horizon)/$close-1），区别于默认 1 日标签。
    """
    factor_df = load_factor_values(factor_expr, start, end, universe)
    # horizon 周期前向收益标签（预测目标），区别于默认 1 日标签
    label_expr = f"Ref($close, -{horizon}) / $close - 1"
    label_df = load_label(start, end, label_expr=label_expr, universe=universe)

    # $close 转 wide（datetime × instrument）用于 horizon 调仓分层净值
    init_qlib()
    from qlib.data import D
    market = universe or settings.quant.get("universe", "csi300")
    instruments = _load_instruments(market)
    close_df = D.features(instruments, ["$close"], start_time=start, end_time=end, freq="day")
    if close_df is None or close_df.empty:
        raise ValueError("$close 价格数据为空，无法计算分层净值")
    prices_df = close_df["$close"].unstack(level="instrument")

    ic_distribution = compute_ic_distribution(factor_df, label_df)
    ic_timeseries = compute_ic_timeseries(factor_df, label_df, ic_window)
    ic_significance = compute_ic_significance(
        pd.Series(ic_timeseries["ic_series"]), lags=horizon
    )
    quantile_nav = compute_quantile_nav_by_horizon(
        factor_df, prices_df, n_groups=n_groups, horizon=horizon
    )
    turnover_curve = compute_turnover_curve(factor_df, n_groups=n_groups, horizon=horizon)
    decay = compute_decay(factor_df, label_df)

    ic_mean = ic_distribution["stats"]["mean"]
    ic_std = ic_distribution["stats"]["std"]
    icir = float(ic_mean / ic_std) if ic_mean is not None and ic_std else None

    decay_lags = sorted(decay.keys())
    return {
        "config": {
            "start": start,
            "end": end,
            "horizon": horizon,
            "n_groups": n_groups,
            "ic_window": ic_window,
        },
        "summary": {
            "ic_mean": ic_mean,
            "ic_std": ic_std,
            "icir": icir,
            "t_stat": ic_significance["t_stat"],
            "p_value": ic_significance["p_value"],
            "significant": ic_significance["significant"],
            "avg_turnover": turnover_curve["avg_turnover"],
            "annual_turnover": turnover_curve["annual_turnover"],
            "long_short_annual_return": quantile_nav["long_short_annual_return"],
            "monotonicity": quantile_nav["monotonicity"],
        },
        "ic_distribution": ic_distribution,
        "ic_timeseries": ic_timeseries,
        "quantile_returns": quantile_nav,
        "turnover_curve": turnover_curve,
        "decay": {"lags": decay_lags, "ic_by_lag": [decay[lag] for lag in decay_lags]},
    }


def compute_orthogonal_ic(
    factor_df: pd.DataFrame,
    baseline_dfs: list,
    label_df: pd.DataFrame,
    factor_col: str = "factor",
    label_col: str = "label",
) -> dict:
    """正交后 IC：候选因子对基准因子做截面回归取残差，再算残差与收益的 IC。

    用于"正交后挖掘"：候选因子若与已有高 IC 因子高度相关，残差 IC 会很低，
    从而筛选出真正有增量 alpha 的因子。

    Args:
        factor_df: 候选因子 MultiIndex(datetime, instrument) DataFrame
        baseline_dfs: 基准因子 DataFrame 列表（已有高 IC 因子）
        label_df: 收益标签 DataFrame
        factor_col / label_col: 列名

    Returns:
        {
            "orthogonal_ic": 残差 IC,
            "orthogonal_rank_ic": 残差 RankIC,
            "r2": 候选因子被基准解释的 R²（高 = 与基准重复）
        }
    """
    if not baseline_dfs:
        return {"orthogonal_ic": None, "orthogonal_rank_ic": None, "r2": None}

    # 合并候选 + 基准到宽表
    wide = factor_df[[factor_col]].rename(columns={factor_col: "candidate"})
    for i, bdf in enumerate(baseline_dfs):
        wide[f"base_{i}"] = bdf[factor_col]
    wide = wide.join(label_df[[label_col]].rename(columns={label_col: "label"}), how="inner").dropna()

    if len(wide) < 10:
        return {"orthogonal_ic": None, "orthogonal_rank_ic": None, "r2": None}

    residuals = []
    dates = wide.index.get_level_values("datetime").unique()
    for dt in dates:
        block = wide.loc[wide.index.get_level_values("datetime") == dt]
        if len(block) < 3:
            continue
        X = block[[c for c in wide.columns if c.startswith("base_")]].values.astype(float)
        y = block["candidate"].values.astype(float)
        # 最小二乘残差（含截距）
        Xc = np.column_stack([np.ones(len(X)), X])
        beta, *_ = np.linalg.lstsq(Xc, y, rcond=None)
        resid = y - Xc @ beta
        tmp = block[["label", "candidate"]].copy()
        tmp["resid"] = resid
        residuals.append(tmp)

    if not residuals:
        return {"orthogonal_ic": None, "orthogonal_rank_ic": None, "r2": None}

    resid_df = pd.concat(residuals)
    # 残差 IC
    ic = float(resid_df["resid"].corr(resid_df["label"])) if len(resid_df) >= 5 else None
    rank_ic = float(resid_df["resid"].corr(resid_df["label"], method="spearman")) if len(resid_df) >= 5 else None
    # R²：候选因子方差中被基准解释的比例（整体，非逐日）
    r2 = None
    if "candidate" in resid_df.columns and len(resid_df) >= 5:
        c = resid_df["candidate"].values.astype(float)
        resid_var = float(np.var(resid_df["resid"].values))
        cand_var = float(np.var(c))
        if cand_var > 1e-12:
            r2 = 1.0 - resid_var / cand_var
    return {
        "orthogonal_ic": round(ic, 4) if ic is not None else None,
        "orthogonal_rank_ic": round(rank_ic, 4) if rank_ic is not None else None,
        "r2": r2,
    }
