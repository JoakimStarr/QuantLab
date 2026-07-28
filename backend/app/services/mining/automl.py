"""④ AutoML 因子组合：用 lightgbm/线性模型学习多因子最优组合，产出综合打分。

与 backtest_engine.combine_factors 的 equal_weight/ic_weight 不同，
AutoML 学习因子间的非线性映射，预测前向收益作为综合打分。
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
from app.models.factor import Factor
from app.services.factor.library import update_factor_metrics, add_factor

logger = logging.getLogger(__name__)


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

        # 加载各因子值（CPU 密集）
        def _load_all():
            frames = []
            names = []
            for f in factors:
                df = load_factor_values(f.expression, start, end).rename(columns={"factor": f.name})
                frames.append(df)
                names.append(f.name)
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
                model = LGBMRegressor(n_estimators=80, max_depth=4, learning_rate=0.05,
                                      min_child_samples=20, verbose=-1)
            model.fit(X_tr, y_tr)
            return model

        model = await asyncio.get_running_loop().run_in_executor(None, _fit)

        # 全量预测作为综合打分
        score = model.predict(merged[names].values)
        score_df = pd.DataFrame({"score": score}, index=merged.index)

        # 评价综合打分的 IC
        from app.services.quant.factor_eval import compute_ic
        label_df_full = merged[["label"]].rename(columns={"label": "label"})
        factor_df = score_df.rename(columns={"score": "factor"})
        # compute_ic 期望 factor/label 列名
        ic_metrics = compute_ic(factor_df, label_df_full)

        # 入库为一个“组合因子”（表达式记录组合方式，实际打分由模型生成）
        combo_desc = f"AutoML组合({method}) of {names}"
        # 表达式用占位（AutoML 因子非简单表达式，记录组合来源）
        expr_repr = f"AutoML({method}," + ",".join(str(i) for i in factor_ids) + ")"
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
        return {"task_id": task_id, "method": method, "factors_used": names,
                "ic": best_ic, "factor_id": factor["id"],
                "ic_metrics": ic_metrics}
    except Exception as e:
        await _update_task(task_id, status="failed", error=str(e)[:500],
                           finished_at=datetime.now())
        raise


async def _update_task(task_id: int, **kwargs):
    from app.services.mining.task_utils import update_task_status
    await update_task_status(task_id, **kwargs)
