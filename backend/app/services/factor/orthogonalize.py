"""因子 PCA 正交化。

使用 PCA 替代 Gram-Schmidt 进行因子正交化，降低因子间共线性对加权组合的干扰。
对每个 datetime 截面，先对因子去均值，再用 PCA 提取正交主成分，
得到相互正交（且不相关）的因子序列。
"""
import logging
import pandas as pd
from sklearn.decomposition import PCA
from typing import Dict, List

logger = logging.getLogger(__name__)


def gram_schmidt_orthogonalize(
    factor_values: Dict[str, pd.DataFrame],
    ic_order: List[str],
    factor_col: str = "factor",
) -> Dict[str, pd.DataFrame]:
    """按 IC 排序对因子做截面 PCA 正交化。

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

    ordered = [n for n in ic_order if n in factor_values]
    if len(ordered) <= 1:
        return factor_values

    series_list = [factor_values[n][factor_col].rename(n) for n in ordered]
    wide = pd.concat(series_list, axis=1)
    # 保持与输入一致的 MultiIndex（instrument, datetime），避免重建索引顺序错位
    wide_names = list(wide.index.names)

    ortho_parts = []
    for dt in wide.index.get_level_values("datetime").unique():
        mask = wide.index.get_level_values("datetime") == dt
        block = wide.loc[mask].dropna()
        if len(block) < 2:
            continue
        M = block.values.astype(float)
        M = M - M.mean(axis=0, keepdims=True)
        pca = PCA(n_components=M.shape[1])
        ortho = pca.fit_transform(M)
        # 直接用原 MultiIndex 子集（名称与顺序不变），pandas 赋值时可正确对齐
        part = pd.DataFrame(ortho, index=block.index, columns=ordered)
        ortho_parts.append(part)

    if not ortho_parts:
        logger.warning("PCA 正交化: 无有效截面数据")
        return factor_values

    ortho_wide = pd.concat(ortho_parts).sort_index()
    ortho_wide.index = ortho_wide.index.set_names(wide_names)

    result = {}
    for name in ordered:
        df = factor_values[name].copy()
        df[factor_col] = ortho_wide[name]
        result[name] = df
    for name, df in factor_values.items():
        if name not in result:
            result[name] = df

    logger.info("PCA 正交化完成: %d 个因子", len(ordered))
    return result
