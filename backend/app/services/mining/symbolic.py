"""② 符号回归/遗传规划挖掘：gplearn 在基础特征上搜索预测性组合，翻译为 qlib 表达式。

流程：
1. 定义基础特征（各自对应 qlib 子表达式）
2. 加载数据构建 X/y（截面+时序展平）
3. gplearn SymbolicRegressor 演化
4. 将最优程序翻译为 qlib 表达式（add->Add, Xi->子表达式）
5. 沙箱校验 + IC 评价 + 入库
"""
import json
import logging
import asyncio
from datetime import datetime
import numpy as np
import pandas as pd
from app.core.database import async_session
from app.core.config import settings
from app.models.mining_task import MiningTask
from app.services.factor.expression import validate_expression, ExpressionValidationError
from app.services.factor.library import add_factor, update_factor_metrics

logger = logging.getLogger(__name__)

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
}

# gplearn 函数名 -> qlib 算子名
_FUNC_MAP = {"add": "Add", "sub": "Sub", "mul": "Mul", "div": "Div"}


def _build_dataset(start: str, end: str) -> tuple:
    """加载基础特征与前向收益，返回 (X, y, feature_names)。"""
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


def _translate_program(prog_str: str, feature_names: list) -> str:
    """将 gplearn 程序字符串翻译为 qlib 表达式。"""
    expr = prog_str
    # 替换函数名（先长后短避免误替）
    for gname in sorted(_FUNC_MAP, key=len, reverse=True):
        expr = expr.replace(gname, _FUNC_MAP[gname])
    # 替换 Xi 为对应子表达式（必须从大索引到小索引，避免 X1 误替 X10/X11 的子串）
    for i in range(len(feature_names) - 1, -1, -1):
        name = feature_names[i]
        sub_expr = _BASE_FEATURES[name]
        expr = expr.replace(f"X{i}", f"({sub_expr})")
    return expr


async def mine_with_symbolic(task_id: int) -> dict:
    """符号回归挖掘主流程。"""
    sym_cfg = settings.mining.get("symbolic", {})
    ic_threshold = sym_cfg.get("ic_threshold", 0.03)
    await _update_task(task_id, status="running", started_at=datetime.now())

    try:
        from gplearn.genetic import SymbolicRegressor
        period = settings.quant.get("default_backtest_period", {})
        start = period.get("start", "2020-01-01")
        end = period.get("end", "2024-12-31")

        # 构建数据集（CPU 密集）
        X, y, feature_names, _ = await asyncio.get_running_loop().run_in_executor(
            None, _build_dataset, start, end
        )
        if len(X) < 100:
            raise ValueError(f"符号回归数据不足: {len(X)} 行")

        est = SymbolicRegressor(
            population_size=sym_cfg.get("population", 1000),
            generations=sym_cfg.get("generations", 20),
            tournament_size=sym_cfg.get("tournament_size", 20),
            parsimony_coefficient=sym_cfg.get("parsimony_coefficient", 0.001),
            function_set=("add", "sub", "mul", "div"),
            n_jobs=1,
            random_state=42,
            verbose=0,
            metric="spearman",  # 秩相关更稳健
        )
        await asyncio.get_running_loop().run_in_executor(None, est.fit, X, y)

        # 收集 Pareto 前沿中的多个程序（这里取 top programs）
        programs = est._programs[-1]  # 最后一代的程序列表
        # 按适应度排序，取前 N 个非平凡的
        valid_progs = [p for p in programs if p is not None and len(p.program) > 1]
        valid_progs.sort(key=lambda p: p.fitness_)
        top_progs = valid_progs[:5]

        passed_ids = []
        best_ic = 0.0
        evaluated = 0
        for prog in top_progs:
            try:
                expr = _translate_program(str(prog), feature_names)
                validate_expression(expr, max_length=10000)
            except ExpressionValidationError as e:
                logger.info("符号回归表达式沙箱拒绝: %s", e)
                continue
            evaluated += 1
            # IC 评价
            from app.services.quant.factor_eval import evaluate_factor
            metrics = await asyncio.get_running_loop().run_in_executor(
                None, evaluate_factor, expr, start, end
            )
            ic = metrics.get("ic")
            if ic is None or abs(ic) < ic_threshold:
                continue
            name = f"sym_{task_id}_{len(passed_ids)}"
            factor = await add_factor(name=name, expression=expr, category="symbolic",
                                      description=f"遗传规划自动发现 (task={task_id})",
                                      source_task_id=task_id, skip_validation=True)
            await update_factor_metrics(factor["id"], metrics)
            passed_ids.append(factor["id"])
            if abs(ic) > abs(best_ic):
                best_ic = ic

        await _update_task(
            task_id, status="done",
            candidates_generated=len(top_progs), candidates_passed=len(passed_ids),
            best_ic=best_ic, result_factor_ids=json.dumps(passed_ids),
            finished_at=datetime.now(),
        )
        return {"task_id": task_id, "evaluated": evaluated,
                "passed": len(passed_ids), "best_ic": best_ic, "factor_ids": passed_ids}
    except Exception as e:
        await _update_task(task_id, status="failed", error=str(e)[:500],
                           finished_at=datetime.now())
        raise


async def _update_task(task_id: int, **kwargs):
    from app.services.mining.task_utils import update_task_status
    await update_task_status(task_id, **kwargs)
