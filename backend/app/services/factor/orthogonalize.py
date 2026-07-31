"""因子 Gram-Schmidt 正交化。

按 IC 绝对值降序对因子做截面正交化：对每个 datetime 截面，先对因子去均值，
再依次将因子对已正交化因子做 OLS 回归取残差，得到相互正交（且不相关）的因子
序列，降低因子间共线性对加权组合的干扰。

采用「先去均值再修正版 Gram-Schmidt」等价于带截距的逐因子截面回归取残差，
正交化后的因子在截面上两两不相关（相关系数为 0），且能经受后续 z-score 标准化。
"""
import logging
import pandas as pd
from typing import Dict, List

logger = logging.getLogger(__name__)


def gram_schmidt_orthogonalize(
    factor_values: Dict[str, pd.DataFrame],
    ic_order: List[str],
    factor_col: str = "factor",
) -> Dict[str, pd.DataFrame]:
    """按 IC 排序对因子做截面 Gram-Schmidt 正交化。

    Args:
        factor_values: {factor_name: DataFrame with factor_col}
            DataFrame index: MultiIndex(datetime, instrument)
        ic_order: 按 IC 绝对值降序排列的因子名列表
        factor_col: 因子值列名

    Returns:
        正交化后的因子值 dict，格式与输入相同；未参与排序的因子原样返回。
    """
    if len(ic_order) <= 1:
        return factor_values

    # 按 ic_order 顺序保留存在输入中的因子
    ordered = [n for n in ic_order if n in factor_values]
    if len(ordered) <= 1:
        return factor_values

    # 构造宽表：index=MultiIndex(datetime, instrument), columns=ordered factor names
    series_list = [factor_values[n][factor_col].rename(n) for n in ordered]
    wide = pd.concat(series_list, axis=1)

    # 逐截面做去均值 + 修正版 Gram-Schmidt
    ortho_parts = []
    for dt in wide.index.get_level_values("datetime").unique():
        # 该截面下所有因子均非空的样本（保证对齐）
        block = wide.xs(dt, level="datetime").dropna()
        if len(block) < 2:
            continue
        M = block.values.astype(float)
        # 截面去均值（等价于带截距回归），保证正交化后因子不相关
        M = M - M.mean(axis=0, keepdims=True)
        Q = M.copy()
        for j in range(M.shape[1]):
            for i in range(j):
                denom = float(Q[:, i] @ Q[:, i])
                if denom < 1e-12:
                    continue
                Q[:, j] -= (Q[:, i] @ Q[:, j]) / denom * Q[:, i]
        # 重建为 MultiIndex(datetime, instrument) 的 DataFrame
        part = pd.DataFrame(Q, index=block.index, columns=ordered)
        part = pd.concat([part], keys=[dt], names=["datetime"])
        ortho_parts.append(part)

    if not ortho_parts:
        logger.warning("Gram-Schmidt 正交化: 无有效截面数据")
        return factor_values

    ortho_wide = pd.concat(ortho_parts).sort_index()

    # 拆回 dict 形式，用正交化结果覆盖 factor_col（按索引对齐，缺失行置 NaN）
    result = {}
    for name in ordered:
        df = factor_values[name].copy()
        df[factor_col] = ortho_wide[name]
        result[name] = df
    # 保留未参与正交化的因子原样返回
    for name, df in factor_values.items():
        if name not in result:
            result[name] = df

    logger.info("Gram-Schmidt 正交化完成: %d 个因子", len(ordered))
    return result
