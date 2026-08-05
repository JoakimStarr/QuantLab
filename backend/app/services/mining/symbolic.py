"""② 符号回归/遗传规划挖掘：gplearn 在基础特征上搜索预测性组合，翻译为 qlib 表达式。

流程：
1. 定义基础特征（各自对应 qlib 子表达式）
2. 加载数据构建 X/y（截面+时序展平）
3. gplearn SymbolicRegressor 演化（扩展函数集 + 早停 + 时序验证集防过拟合）
4. 将最优程序翻译为 qlib 表达式（add->Add, Xi->子表达式）
5. 沙箱校验 + IC 评价 + 入库
"""
import json
import logging
import asyncio
import os
from datetime import datetime
import numpy as np
import pandas as pd
from app.core.config import settings
from app.core.gpu_utils import is_gpu_available, get_device
from app.services.factor.expression import validate_expression, ExpressionValidationError
from app.services.factor.library import add_factor, update_factor_metrics
from app.services.mining.task_utils import update_task_status as _update_task

logger = logging.getLogger(__name__)

# 启动时检测 GPU（gplearn 本身不支持 GPU，但可据此调整并行度）
_HAS_GPU = is_gpu_available()
_DEVICE = get_device()

# 基础特征：name -> qlib 子表达式（作为遗传规划的"终端"）
# 注意：Ref 正数=过去，负数=未来；因子只能用过去数据，避免 look-ahead bias
_BASE_FEATURES = {
    "mom_5": "$close / Ref($close, 5) - 1",
    "mom_20": "$close / Ref($close, 20) - 1",
    "vol_20": "Std($close / Ref($close, 1) - 1, 20)",
    "vol_60": "Std($close / Ref($close, 1) - 1, 60)",
    "turn_5": "Mean($volume, 5)",
    "turn_20": "Mean($volume, 20)",
    "vratio": "Mean($volume, 5) / Mean($volume, 20)",
    "amp_20": "Mean(($high - $low) / $close, 20)",
    "ma_div_20": "$close / Mean($close, 20) - 1",
    "ma_div_60": "$close / Mean($close, 60) - 1",
    "high_dd_20": "$close / Max($close, 20) - 1",
    "rsi_20": "Mean(Greater($close / Ref($close, 1) - 1, 0), 20)",
    # 高阶矩与回归特征（tsfresh 风格时序特征，qlib 原生可表达，可直接翻译）
    "skew_20": "Skew($close / Ref($close, 1) - 1, 20)",
    "kurt_20": "Kurt($close / Ref($close, 1) - 1, 20)",
    "sum_ret_20": "Sum($close / Ref($close, 1) - 1, 20)",
    "slope_20": "Slope($close, 20)",
}

# gplearn 函数名 -> qlib 算子名（max/min/if/log 语义不同，在 _translate_tree 中单独展开）
_FUNC_MAP = {
    "add": "Add", "sub": "Sub", "mul": "Mul", "div": "Div",
    "abs": "Abs", "sign": "Sign",
}


def _build_function_set():
    """构建扩展的 gplearn 函数集：基础四则运算 + log/abs/sign/max/min/if。

    使用 make_function 包装自定义函数，保证 gplearn 可识别。
    """
    from gplearn.functions import make_function

    def _protected_log(x):
        """对负值取绝对值再 log，加 epsilon 防止 log(0)。"""
        return np.log(np.abs(x) + 1e-6)

    def _abs(x):
        return np.abs(x)

    def _sign(x):
        return np.sign(x)

    def _max(x1, x2):
        return np.maximum(x1, x2)

    def _min(x1, x2):
        return np.minimum(x1, x2)

    def _if(cond, a, b):
        """条件判断：if cond > 0 then a else b。"""
        return np.where(cond > 0, a, b)

    protected_log = make_function(function=_protected_log, name="log", arity=1)
    abs_func = make_function(function=_abs, name="abs", arity=1)
    sign_func = make_function(function=_sign, name="sign", arity=1)
    max_func = make_function(function=_max, name="max", arity=2)
    min_func = make_function(function=_min, name="min", arity=2)
    if_func = make_function(function=_if, name="if", arity=3)

    return (
        "add", "sub", "mul", "div",
        protected_log, abs_func, sign_func, max_func, min_func, if_func,
    )


def _spearman_ic(a, b):
    """计算 Spearman 秩相关 IC（用于 train/valid 过拟合检测）。"""
    if len(a) < 2:
        return 0.0
    a_rank = pd.Series(a).rank().values
    b_rank = pd.Series(b).rank().values
    corr = np.corrcoef(a_rank, b_rank)[0, 1]
    return float(corr) if not np.isnan(corr) else 0.0


def _build_dataset(start: str, end: str) -> tuple:
    """加载基础特征与前向收益，返回 (X, y, feature_names, merged_index)。"""
    from app.services.quant.factor_eval import load_factor_values, load_label
    feature_names = list(_BASE_FEATURES.keys())
    frames = []
    for name in feature_names:
        df = load_factor_values(_BASE_FEATURES[name], start, end)
        df = df.rename(columns={"factor": name})
        frames.append(df)
    X_df = pd.concat(frames, axis=1)
    label_df = load_label(start, end)
    merged = X_df.join(label_df, how="inner").dropna()
    y = merged["label"].values
    X = merged[feature_names].values
    return X, y, feature_names, merged.index


def _translate_tree(node, feature_names: list) -> str:
    """将 gplearn 程序树递归翻译为 qlib 表达式（不依赖字符串替换，语义精确）。

    gplearn Program.program 为嵌套结构：
    - 叶子: str 'X{n}'（特征终端）或 float（常数）
    - 节点: [func, arg1, arg2, ...]（gplearn 用 list；测试可用 tuple），
      func 为 gplearn Function 对象（含 .name）或 str

    关键语义映射（gplearn 实现 → qlib 等价语义）：
    - max(a,b) = np.maximum(a,b)         → If(Greater(a,b), a, b)
    - min(a,b) = np.minimum(a,b)         → If(Less(a,b), a, b)
    - if(c,a,b) = np.where(c>0, a, b)    → If(Greater(c,0), a, b)
      注意 qlib If(cond,a,b) 是 cond!=0 取 a，与 gplearn 的 cond>0 不同，
      必须显式 Greater(c,0) 保持原语义。
    """
    if isinstance(node, (int, float, np.integer, np.floating)):
        return repr(float(node))
    if isinstance(node, str):
        if node.startswith("X"):
            try:
                idx = int(node[1:])
            except ValueError:
                raise ValueError(f"无法解析 gplearn 终端: {node}")
            if not 0 <= idx < len(feature_names):
                raise ValueError(f"gplearn 终端 {node} 超出特征范围 {len(feature_names)}")
            return f"({_BASE_FEATURES[feature_names[idx]]})"
        raise ValueError(f"无法解析 gplearn 终端: {node}")
    if not isinstance(node, (tuple, list)) or not node:
        raise ValueError(f"无法解析 gplearn 节点: {node!r}")

    func = node[0]
    args = node[1:]
    gname = getattr(func, "name", None) or str(func)
    targs = [_translate_tree(a, feature_names) for a in args]

    # 常数折叠：子树全为常数时直接用 gplearn 函数数值计算（语义精确对齐，
    # 含 protected division 除零返回 1.0）。折叠后常数只出现在二元算子参数位
    # （qlib PairOperator 对数字有防护）或 If 分支（有防护），否则 Abs(0.5)
    # 这类"常数作为一元算子参数"会在 qlib 求值时崩
    # （ElemOperator.get_extended_window_size 无数字防护）。
    if all(_is_number(t) for t in targs):
        vals = [float(t) for t in targs]
        folded = None
        if hasattr(func, "__call__"):
            try:
                folded = float(func(*vals))
            except (TypeError, ValueError):
                folded = None
        if folded is None or not np.isfinite(folded):
            folded = _fold_const_named(gname, vals)
        if folded is not None:
            return repr(folded)

    if gname == "max":
        if len(targs) != 2:
            raise ValueError(f"max 需要 2 个参数，实际 {len(targs)}")
        return f"If(Greater({targs[0]}, {targs[1]}), {targs[0]}, {targs[1]})"
    if gname == "min":
        if len(targs) != 2:
            raise ValueError(f"min 需要 2 个参数，实际 {len(targs)}")
        return f"If(Less({targs[0]}, {targs[1]}), {targs[0]}, {targs[1]})"
    if gname == "if":
        if len(targs) != 3:
            raise ValueError(f"if 需要 3 个参数，实际 {len(targs)}")
        return f"If(Greater({targs[0]}, 0), {targs[1]}, {targs[2]})"
    if gname == "log":
        if len(targs) != 1:
            raise ValueError(f"log 需要 1 个参数，实际 {len(targs)}")
        # 与 gplearn protected_log 对齐：log(abs(x)+eps)，避免负数/零导致 NaN
        return f"Log(Abs({targs[0]}) + 1e-6)"
    if gname not in _FUNC_MAP:
        raise ValueError(f"gplearn 函数 {gname} 无 qlib 映射")
    return f"{_FUNC_MAP[gname]}({', '.join(targs)})"


def _translate_postfix(flat: list, feature_names: list) -> str:
    """将 gplearn 扁平后序程序列表翻译为 qlib 表达式。

    gplearn 的 _Program.program 实际是扁平的 postfix 列表：
    [add, add, 0, 1, 1, 0.079]
    - _Function 对象（含 .arity/.name）为函数节点
    - int 为特征索引（不是 'X{n}' 字符串！）
    - float 为常数叶

    规约逻辑与 gplearn _Program.execute 一致：函数项压栈，参数齐（arity+1）即规约。
    """
    root = None
    apply_stack = []  # 每项: [func_name, arity, arg...]，终端以裸值追加到栈顶函数项
    if len(flat) == 1:
        # 单节点程序（纯终端：特征索引或常数）
        item = flat[0]
        if isinstance(item, (int, np.integer)):
            idx = int(item)
            if not 0 <= idx < len(feature_names):
                raise ValueError(f"gplearn 终端 X{idx} 超出特征范围 {len(feature_names)}")
            return f"({_BASE_FEATURES[feature_names[idx]]})"
        if isinstance(item, (float, np.floating)):
            return repr(float(item))
        raise ValueError(f"无法解析 gplearn 节点: {item!r}")
    for item in flat:
        if hasattr(item, "arity"):
            apply_stack.append([getattr(item, "name", str(item)), item.arity])
        else:
            if not apply_stack:
                raise ValueError(f"gplearn 表达式无根函数: {item!r}")
            if not isinstance(item, (int, np.integer, float, np.floating)):
                raise ValueError(f"无法解析 gplearn 节点: {item!r}")
            apply_stack[-1].append(int(item) if isinstance(item, (int, np.integer)) else float(item))

        while apply_stack and len(apply_stack[-1]) == apply_stack[-1][1] + 2:
            entry = apply_stack.pop()
            name = entry[0]
            args = []
            for a in entry[2:]:
                if isinstance(a, (int, np.integer)):
                    args.append(f"X{int(a)}")
                elif isinstance(a, (float, np.floating)):
                    args.append(float(a))
                else:
                    args.append(a)
            node = (name,) + tuple(args)
            if apply_stack:
                apply_stack[-1].append(node)
            else:
                root = node
    if root is None:
        raise ValueError("gplearn 表达式为空")
    return _translate_tree(root, feature_names)


def _translate_program(prog, feature_names: list) -> str:
    """将 gplearn Program 对象翻译为 qlib 表达式。

    Args:
        prog: gplearn Program（含 .program 属性）或裸嵌套结构（测试用）
    """
    tree = prog.program if hasattr(prog, "program") else prog
    # gplearn 真实输出为扁平后序列表（元素无嵌套 list/tuple）；嵌套结构走树翻译
    if isinstance(tree, list) and tree and not any(
        isinstance(x, (list, tuple)) for x in tree
    ):
        return _translate_postfix(tree, feature_names)
    return _translate_tree(tree, feature_names)


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def _fold_const_named(gname: str, vals: list) -> float | None:
    """按函数名做常数折叠（字符串函数名兜底，与 _build_function_set 语义一致）。"""
    if gname == "add":
        return vals[0] + vals[1]
    if gname == "sub":
        return vals[0] - vals[1]
    if gname == "mul":
        return vals[0] * vals[1]
    if gname == "div":
        # gplearn protected division: x1/x2 if x2 != 0 else 1.0
        return vals[0] / vals[1] if vals[1] != 0 else 1.0
    if gname == "abs":
        return abs(vals[0])
    if gname == "sign":
        return float(np.sign(vals[0]))
    if gname == "log":
        return float(np.log(abs(vals[0]) + 1e-6))
    if gname == "max":
        return max(vals[0], vals[1])
    if gname == "min":
        return min(vals[0], vals[1])
    if gname == "if":
        return float(np.where(vals[0] > 0, vals[1], vals[2]))
    return None


async def mine_with_symbolic(task_id: int) -> dict:
    """符号回归挖掘主流程（使用多维验证 + GPU 检测）。"""
    sym_cfg = settings.mining.get("symbolic", {})
    ic_threshold = sym_cfg.get("ic_threshold", 0.03)
    horizon = sym_cfg.get("eval_horizon") or settings.mining.get("llm", {}).get("eval_horizon", 5)
    await _update_task(task_id, status="running", started_at=datetime.now())

    if _HAS_GPU:
        logger.info("符号回归: GPU 可用（设备: %s），使用并行训练", _DEVICE)

    try:
        from gplearn.genetic import SymbolicRegressor
        period = settings.quant.get("default_backtest_period", {})
        start = period.get("start", "2020-01-01")
        end = period.get("end", "2024-12-31")

        # 构建数据集（CPU 密集）
        X, y, feature_names, merged_index = await asyncio.get_running_loop().run_in_executor(
            None, _build_dataset, start, end
        )
        if len(X) < 100:
            raise ValueError(f"符号回归数据不足: {len(X)} 行")

        # 时序分割：按日期切，最后 20% 日期作为验证集（整截面归一侧，避免截面泄露）
        dates = sorted(merged_index.get_level_values("datetime").unique())
        split_pos = int(len(dates) * 0.8)
        train_dates = set(dates[:split_pos])
        row_dates = merged_index.get_level_values("datetime")
        train_mask = row_dates.isin(train_dates)
        X_train, X_valid = X[train_mask], X[~train_mask]
        y_train, y_valid = y[train_mask], y[~train_mask]
        logger.info("符号回归时序分割(按日期): train=%d, valid=%d", len(X_train), len(X_valid))

        # 构建扩展函数集
        function_set = _build_function_set()

        # 有 GPU 时用全核（gplearn 不支持 GPU，n_jobs 并行评估种群）；
        # 无 GPU 时默认用一半核数（上限 4），n_jobs=1 串行演化太慢。
        default_n_jobs = -1 if _HAS_GPU else min(4, max(1, (os.cpu_count() or 2) // 2))
        n_jobs = sym_cfg.get("n_jobs", default_n_jobs)
        logger.info("符号回归: n_jobs=%d (GPU=%s)", n_jobs, _HAS_GPU)

        est = SymbolicRegressor(
            population_size=sym_cfg.get("population", 1000),
            generations=sym_cfg.get("generations", 30),
            tournament_size=sym_cfg.get("tournament_size", 20),
            parsimony_coefficient=sym_cfg.get("parsimony_coefficient", 0.001),
            # 限制树深：特征叶子展开为基础子表达式（如 Std(...,60)），
            # 树深过深会指数膨胀导致表达式超沙箱节点/长度上限
            init_depth=tuple(sym_cfg.get("init_depth", (2, 4))),
            function_set=function_set,
            const_range=(-1.0, 1.0),  # 启用常数项
            stopping_criteria=0.01,  # 早停
            n_jobs=n_jobs,
            random_state=42,
            verbose=0,
            metric="spearman",  # 秩相关更稳健
        )
        await asyncio.get_running_loop().run_in_executor(None, est.fit, X_train, y_train)

        # 计算 train/valid IC，检测过拟合（train IC - valid IC > 0.05 视为过拟合）
        train_pred = est.predict(X_train)
        valid_pred = est.predict(X_valid) if len(X_valid) > 0 else np.array([])
        train_ic = _spearman_ic(train_pred, y_train)
        valid_ic = _spearman_ic(valid_pred, y_valid) if len(valid_pred) > 0 else 0.0
        overfit = (train_ic - valid_ic) > 0.05
        logger.info("符号回归 IC: train=%.4f, valid=%.4f, overfit=%s",
                    train_ic, valid_ic, overfit)

        # 收集 Pareto 前沿中的多个程序（这里取 top programs）
        programs = est._programs[-1]  # 最后一代的程序列表
        # 按适应度排序，取前 N 个非平凡的
        valid_progs = [p for p in programs if p is not None and len(p.program) > 1]
        valid_progs.sort(key=lambda p: p.fitness_)
        top_progs = valid_progs[:5]

        passed_ids = []
        best_ic = 0.0
        evaluated = 0
        # 样本外评价区间（验证段日期），入库 IC 只用 OOS，避免 in-sample 高估
        valid_start = str(dates[split_pos].date())
        valid_end = str(dates[-1].date())
        # 多样性检测：加载已有因子 IC 序列（缓存，仅一次）
        from app.services.mining.llm_factor import _load_existing_ic_series
        existing_ic_series = await _load_existing_ic_series()

        # 第一遍：沙箱校验 + 评价（收集所有候选结果，便于 BH 校正）
        candidates = []
        for prog in top_progs:
            try:
                expr = _translate_program(prog, feature_names)
                validate_expression(expr, max_length=10000)
            except ExpressionValidationError as e:
                logger.info("符号回归表达式沙箱拒绝: %s", e)
                continue
            evaluated += 1
            # 多维验证：样本分割 + 滚动 IC + 统计显著性 + 多样性
            from app.services.quant.factor_validator import evaluate_factor_with_validation
            from app.core.executor import run_cpu
            metrics = await run_cpu(
                evaluate_factor_with_validation, expr, valid_start, valid_end,
                horizon=horizon, existing_ic_series=existing_ic_series,
            )
            candidates.append((expr, metrics))

        # BH 多重检验校正
        from app.services.quant.factor_validator import bh_corrected_pvalues
        significance_alpha = settings.mining.get("llm", {}).get("significance_alpha", 0.05)
        p_vals = [m.get("significance", {}).get("p_value") if m else None for _, m in candidates]
        p_adj = bh_corrected_pvalues(p_vals)
        for (_, m), pa in zip(candidates, p_adj):
            if m and pa is not None:
                m = dict(m)
                m["significance"] = {**(m.get("significance") or {}), "p_adj": pa}
                m["p_adj"] = pa

        # 第二遍：BH 筛选 + 入库
        for idx, (expr, metrics) in enumerate(candidates):
            if metrics is None:
                continue
            sig = metrics.get("significance") or {}
            p_adj_val = sig.get("p_adj")
            bh_ok = p_adj_val is None or p_adj_val < significance_alpha
            # 使用 valid_ic 作为主筛选指标；不做全样本 IC 兜底（稳定性/显著性等未过
            # 说明因子不稳健，全样本 IC 达标属过拟合信号）
            valid_ic_val = metrics.get("valid_ic")
            passed = metrics.get("passed", False)
            if not (passed and valid_ic_val is not None
                    and abs(valid_ic_val) >= ic_threshold and bh_ok):
                if not bh_ok:
                    logger.info("符号回归因子 %s 未通过 BH 校正: p_adj=%s", expr[:40], p_adj_val)
                else:
                    logger.info("符号回归因子 %s 未通过多维验证: valid_ic=%s, 原因: %s",
                                expr[:40], valid_ic_val,
                                "; ".join((metrics.get("fail_reasons") or [])[:3]))
                continue
            # 过拟合标记与样本标注写入 metrics
            metrics["train_ic"] = train_ic
            metrics["gplearn_valid_ic"] = valid_ic
            metrics["overfit"] = overfit
            metrics["sample"] = "out-of-sample"
            name = f"sym_{task_id}_{len(passed_ids)}"
            factor = await add_factor(name=name, expression=expr, category="symbolic",
                                      description=f"遗传规划自动发现 (task={task_id}, overfit={overfit})",
                                      source_task_id=task_id, skip_validation=True)
            await update_factor_metrics(factor["id"], metrics)
            passed_ids.append(factor["id"])
            if abs(valid_ic_val) > abs(best_ic):
                best_ic = valid_ic_val

        await _update_task(
            task_id, status="done",
            candidates_generated=len(top_progs), candidates_passed=len(passed_ids),
            best_ic=best_ic, result_factor_ids=json.dumps(passed_ids),
            finished_at=datetime.now(),
        )
        return {"task_id": task_id, "evaluated": evaluated,
                "passed": len(passed_ids), "best_ic": best_ic, "factor_ids": passed_ids,
                "train_ic": train_ic, "gplearn_valid_ic": valid_ic, "overfit": overfit}
    except Exception as e:
        await _update_task(task_id, status="failed", error=str(e)[:500],
                           finished_at=datetime.now())
        raise
