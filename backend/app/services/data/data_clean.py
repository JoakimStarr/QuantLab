"""数据清洗公共工具：float 转换、日期规范化。

收拢各数据同步模块里重复的 ``_f`` / ``_to_float`` / ``_clean_num`` 与
``pd.to_datetime(...).dt.strftime("%Y-%m-%d")`` 逻辑，保证行为一致、单点修改。

- ``to_float``：宽松转换，容忍 %、千分位逗号、None、NaN（原 macro/fundamental ``_to_float``）。
- ``to_float_strict``：仅数值转换，NaN/None/非法 → None（原 backfill/etf_sync ``_f``、data_ext ``_clean_num``）。
- ``format_date_series``：把日期列统一格式化为 ``YYYY-MM-DD`` 字符串。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def to_float(val) -> float | None:
    """宽松转 float：容忍 %、千分位逗号、None、NaN。"""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        try:
            return float(val) if not pd.isna(val) else None
        except (TypeError, ValueError):
            return None
    s = str(val).strip().replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def to_float_strict(v) -> float | None:
    """简单 float 转换，NaN/None/非法 → None（数值型字段专用）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if isinstance(f, float) and np.isnan(f):
        return None
    return f


def format_date_series(series) -> pd.Series:
    """把日期列格式化为 YYYY-MM-DD 字符串（容忍 datetime/str/NaT）。"""
    return pd.to_datetime(series).dt.strftime("%Y-%m-%d")
