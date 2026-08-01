"""④ AutoML 因子组合：用 lightgbm/线性模型学习多因子最优组合，产出综合打分。

与 backtest_engine.combine_factors 的 equal_weight/ic_weight 不同，
AutoML 学习因子间的非线性映射，预测前向收益作为综合打分。

增强：
- 模型持久化：训练后 joblib 序列化到 data/models/automl/{task_id}.pkl（含特征元信息 bundle）
- SHAP 特征重要性：训练后计算并写入 mining_task 的 params.result
- 时序交叉验证：time_series_cv_eval
- 回测支持：load_factor_values 遇到 AutoML(method,task_id) 时加载 bundle 重建特征预测
"""
import json
import logging
import asyncio
import os
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
from app.core.database import async_session
from app.core.config import settings
from app.core.gpu_utils import is_gpu_available, get_device
from app.models.mining_task import MiningTask
from app.models.factor import Factor
from app.services.factor.library import update_factor_metrics, add_factor
from app.services.mining.task_utils import update_task_status as _update_task

logger = logging.getLogger(__name__)

# 启动时检测 GPU
_HAS_GPU = is_gpu_available()
_DEVICE = get_device()

# 模型持久化目录（锚定项目根，避免受 CWD 影响）
# automl.py 位于 backend/app/services/mining/，项目根为 parents[4]
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
MODELS_DIR = _PROJECT_ROOT / "data" / "models" / "automl"


def _model_path(task_id) -> str:
    return str(MODELS_DIR / f"{task_id}.pkl")


async def mine_with_automl(task_id: int, factor_ids: list[int], method: str = None) -> dict:
    """AutoML 因子组合：训练模型预测收益，综合打分作为新因子入库。"""
    from app.services.quant.qlib_init import init_qlib
    from app.services.quant.factor_eval import load_factor_values, load_label

    automl_cfg = settings.mining.get("automl", {})
    method = method or automl_cfg.get("combo_method", "lightgbm")
    await _update_task(task_id, status="running", started_at=datetime.now())

    try:
        init_qlib()
        period = settings.quant.get("default_backtest_period", {})
        start = period.get("start", "2020-01-01")
        end = period.get("end", "2024-12-31")

        # 加载因子元数据
        from sqlalchemy import select
        async with async_session() as session:
            result = await session.execute(select(Factor).where(Factor.id.in_(factor_ids)))
            factors = result.scalars().all()
        if not factors:
            raise ValueError("未提供有效因子")

        # 基础因子表达式（用于 bundle，回测时按名重建特征）
        factor_expressions = {f.name: f.expression for f in factors}

        # 加载各因子值（CPU 密集）
        def _load_all():
            frames = []
            names = []
            skipped = []
            for f in factors:
                try:
                    df = load_factor_values(f.expression, start, end).rename(columns={"factor": f.name})
                    frames.append(df)
                    names.append(f.name)
                except (FileNotFoundError, ValueError) as e:
                    # AutoML bundle 丢失 / 文本算子不可用：跳过该因子而非整任务失败
                    logger.warning("AutoML 跳过因子 %s (id=%s): %s", f.name, f.id, e)
                    skipped.append(f.name)
            if not frames:
                raise ValueError(f"所有基础因子均不可用，无法训练 AutoML（跳过: {skipped}）")
            if skipped:
                logger.warning("AutoML 训练跳过 %d 个不可用因子: %s", len(skipped), skipped)
            X_df = pd.concat(frames, axis=1)
            label_df = load_label(start, end)
            return X_df, label_df, names

        X_df, label_df, names = await asyncio.get_running_loop().run_in_executor(None, _load_all)
        merged = X_df.join(label_df, how="inner").dropna()
        if len(merged) < 200:
            raise ValueError(f"AutoML 数据不足: {len(merged)} 行")

        # 截面标准化
        for n in names:
            merged[n] = merged.groupby(level="datetime")[n].transform(
                lambda x: (x - x.mean()) / (x.std(ddof=0) + 1e-8)
            )

        # 时序切分（训练/验证）
        dates = merged.index.get_level_values("datetime").unique().sort_values()
        split = int(len(dates) * 0.7)
        train_dates = dates[:split]
        is_train = merged.index.get_level_values("datetime").isin(train_dates)
        train = merged[is_train]
        valid = merged[~is_train]

        X_tr, y_tr = train[names].values, train["label"].values
        X_val, y_val = valid[names].values, valid["label"].values

        # 训练
        def _fit():
            if method == "linear":
                from sklearn.linear_model import Ridge
                model = Ridge(alpha=1.0)
            else:
                from lightgbm import LGBMRegressor
                lgb_params = dict(
                    n_estimators=80, max_depth=4, learning_rate=0.05,
                    min_child_samples=20, verbose=-1,
                )
                if _HAS_GPU:
                    lgb_params["device"] = "gpu"
                    lgb_params["gpu_device_id"] = 0
                    logger.info("AutoML 使用 GPU 训练 LightGBM")
                model = LGBMRegressor(**lgb_params)
            model.fit(X_tr, y_tr)
            return model

        model = await asyncio.get_running_loop().run_in_executor(None, _fit)

        # 模型持久化（bundle 含特征元信息，回测时自包含重建特征）
        model_path = _model_path(task_id)
        os.makedirs(os.path.dirname(model_path), exist_ok=True)
        import joblib
        # 时序交叉验证（评估泛化性）
        cv_result = None
        try:
            def _model_factory():
                if method == "linear":
                    from sklearn.linear_model import Ridge
                    return Ridge(alpha=1.0)
                else:
                    from lightgbm import LGBMRegressor
                    lgb_params = dict(
                        n_estimators=80, max_depth=4, learning_rate=0.05,
                        min_child_samples=20, verbose=-1,
                    )
                    if _HAS_GPU:
                        lgb_params["device"] = "gpu"
                        lgb_params["gpu_device_id"] = 0
                    return LGBMRegressor(**lgb_params)
            cv_result = await asyncio.get_running_loop().run_in_executor(
                None, lambda: time_series_cv_eval(_model_factory, merged[names], merged["label"], n_splits=5)
            )
            logger.info("AutoML 时序CV: mean_ic=%.4f, std_ic=%.4f", cv_result["mean_ic"], cv_result["std_ic"])
        except Exception as e:
            logger.warning("AutoML 时序CV失败: %s", e)
            cv_result = {"mean_ic": 0, "std_ic": 0, "scores": [], "error": str(e)}

        # SHAP 特征重要性
        shap_importance_sorted = _compute_shap(model, method, X_tr, X_val, names)
        bundle = {
            "model": model,
            "method": method,
            "task_id": task_id,
            "feature_names": names,
            "factor_ids": list(factor_ids),
            "factor_expressions": factor_expressions,
            "shap_importance": shap_importance_sorted,
            "trained_at": datetime.now().isoformat(),
        }
        joblib.dump(bundle, model_path)
        logger.info("AutoML 模型已持久化: %s", model_path)

        # 样本外评价：仅在验证段预测并计算 IC，避免 in-sample 高估
        from app.services.quant.factor_eval import compute_ic
        val_score = model.predict(X_val)
        val_factor_df = pd.DataFrame({"factor": val_score}, index=valid.index)
        val_label_df = valid[["label"]].rename(columns={"label": "label"})
        ic_metrics = compute_ic(val_factor_df, val_label_df)
        ic_metrics["sample"] = "out-of-sample"
        # 全量预测留作综合打分参考（不入库 IC）
        score = model.predict(merged[names].values)
        score_df = pd.DataFrame({"score": score}, index=merged.index)

        # 入库为一个“组合因子”（表达式记录模型路径引用：AutoML(method,task_id)）
        combo_desc = f"AutoML组合({method}) of {names}"
        # 表达式引用 task_id，回测引擎解析后加载 bundle 预测
        expr_repr = f"AutoML({method},{task_id})"
        factor = await add_factor(name=f"automl_{task_id}", expression=expr_repr,
                                  category="automl", description=combo_desc,
                                  source_task_id=task_id, skip_validation=True)
        await update_factor_metrics(factor["id"], ic_metrics)

        best_ic = ic_metrics.get("ic") or 0.0
        await _update_task(
            task_id, status="done", candidates_generated=1, candidates_passed=1,
            best_ic=best_ic, result_factor_ids=json.dumps([factor["id"]]),
            finished_at=datetime.now(),
        )
        # 将 SHAP 重要性 / 模型路径 / 时序CV 写入 mining_task 的 params.result（无独立 result 列）
        await _save_task_result(task_id, {
            "model_path": model_path,
            "method": method,
            "feature_names": names,
            "shap_importance": shap_importance_sorted,
            "ic_metrics": ic_metrics,
            "cv_result": cv_result,
        })
        return {"task_id": task_id, "method": method, "factors_used": names,
                "ic": best_ic, "factor_id": factor["id"],
                "ic_metrics": ic_metrics,
                "model_path": model_path,
                "shap_importance": shap_importance_sorted,
                "cv_result": cv_result}
    except Exception as e:
        await _update_task(task_id, status="failed", error=str(e)[:500],
                           finished_at=datetime.now())
        raise


def _compute_shap(model, method: str, X_train: np.ndarray, X_valid: np.ndarray,
                  feature_names: list) -> list:
    """计算 SHAP 特征重要性，返回 [(feature, importance), ...] 降序。"""
    try:
        import shap
        if method == "linear":
            explainer = shap.LinearExplainer(model, X_train)
            shap_values = explainer.shap_values(X_valid)
        else:
            explainer = shap.TreeExplainer(model)
            shap_values = explainer.shap_values(X_valid)
        # 兼容多输出/嵌套结构
        if isinstance(shap_values, list):
            shap_values = shap_values[0]
        shap_values = np.asarray(shap_values)
        feature_importance = np.abs(shap_values).mean(axis=0)
        importance_dict = dict(zip(feature_names, feature_importance.tolist()))
        importance_sorted = sorted(importance_dict.items(), key=lambda x: x[1], reverse=True)
        return importance_sorted
    except Exception as e:
        logger.warning("SHAP 计算失败: %s", e)
        return []


# ---------------- 模型加载器（回测时加载 AutoML 因子） ----------------

def load_automl_bundle(task_id) -> dict:
    """加载已训练的 AutoML 模型包（含模型、特征名、基础因子表达式等元信息）。"""
    import joblib
    model_path = _model_path(task_id)
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"模型文件不存在: {model_path}")
    return joblib.load(model_path)


def load_automl_model(task_id):
    """加载已训练的 AutoML 模型（返回模型对象，可直接 predict）。"""
    return load_automl_bundle(task_id)["model"]


def predict_with_automl_model(task_id, features_df: pd.DataFrame) -> pd.Series:
    """使用已训练的模型预测；自动按训练时特征顺序对齐列。"""
    bundle = load_automl_bundle(task_id)
    model = bundle["model"]
    feature_names = bundle.get("feature_names") or list(features_df.columns)
    # 对齐列顺序，缺失列报错
    missing = [c for c in feature_names if c not in features_df.columns]
    if missing:
        raise ValueError(f"特征缺失，无法预测: {missing}")
    X = features_df[feature_names]
    predictions = model.predict(X.values)
    return pd.Series(predictions, index=features_df.index)


# ---------------- 时序交叉验证 ----------------

def time_series_cv_eval(model_factory, X: pd.DataFrame, y: pd.Series, n_splits: int = 5) -> dict:
    """时序交叉验证（TimeSeriesSplit），返回各折 Spearman IC 统计。

    Args:
        model_factory: 无参 callable，每次调用返回新模型实例
        X: 特征 DataFrame
        y: 标签 Series（与 X 同索引）
        n_splits: 折数
    """
    from sklearn.model_selection import TimeSeriesSplit
    from scipy.stats import spearmanr

    tscv = TimeSeriesSplit(n_splits=n_splits)
    cv_scores = []
    X_arr = X.values if hasattr(X, "values") else np.asarray(X)
    y_arr = y.values if hasattr(y, "values") else np.asarray(y)
    for train_idx, valid_idx in tscv.split(X_arr):
        model = model_factory()
        model.fit(X_arr[train_idx], y_arr[train_idx])
        pred = model.predict(X_arr[valid_idx])
        if len(pred) > 1:
            ic, _ = spearmanr(pred, y_arr[valid_idx])
            cv_scores.append(float(ic) if not np.isnan(ic) else 0.0)
    return {
        "mean_ic": float(np.mean(cv_scores)) if cv_scores else 0.0,
        "std_ic": float(np.std(cv_scores)) if cv_scores else 0.0,
        "scores": cv_scores,
    }


async def _save_task_result(task_id: int, result: dict) -> None:
    """将结果（SHAP/模型路径等）合并写入 mining_task.params.result。

    MiningTask 无独立 result 列，复用 params(JSON) 存放结构化结果。
    """
    async with async_session() as session:
        t = await session.get(MiningTask, task_id)
        if t is None:
            return
        params = json.loads(t.params) if t.params else {}
        params.setdefault("result", {})
        # result 中可能含不可序列化对象，统一 json 往返清洗
        params["result"].update(json.loads(json.dumps(result, default=str)))
        t.params = json.dumps(params, ensure_ascii=False)
        await session.commit()

