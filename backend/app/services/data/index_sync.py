"""指数行情同步到 qlib bin 格式（baostock 数据源）

通过 baostock 的 query_history_k_data_plus 拉取指数日K数据，
转换为 qlib bin 格式写入 features 目录。

支持指数清单由 config.quant.sync_indices 配置（qlib 代码格式，如 sh000001），
默认包含：上证指数、沪深300、上证50、中证500、中证1000、深证成指、创业板指、科创50。

替代原 akshare 版本，避免 akshare 反爬问题；baostock 一次登录可批量拉取且不限频。
指数同步不扩展日历（chenditc 日历已完整），仅按现有日历对齐写入。
"""
import os
import logging
import numpy as np
import pandas as pd
from datetime import datetime

from app.core.config import settings
from app.services.data.eod_incremental import (
    _write_bin,
    _get_calendar,
)

logger = logging.getLogger(__name__)

# 默认指数清单（当 config.quant.sync_indices 未配置时使用）
DEFAULT_INDEX_LIST = [
    "sh000001",  # 上证指数
    "sh000300",  # 沪深300
    "sh000016",  # 上证50
    "sh000905",  # 中证500
    "sh000852",  # 中证1000
    "sz399001",  # 深证成指
    "sz399006",  # 创业板指
    "sh000688",  # 科创50
]

# qlib 指数字段（与 chenditc 指数 bin 一致：open/high/low/close/volume）
INDEX_FIELDS = ["open", "high", "low", "close", "volume"]


def _get_index_list(indices: list = None) -> list:
    """获取指数清单：优先参数 > config.quant.sync_indices > 默认 8 大指数"""
    if indices is not None:
        return indices
    cfg_list = (settings.quant or {}).get("sync_indices")
    if cfg_list:
        return list(cfg_list)
    return DEFAULT_INDEX_LIST


def _fetch_index_via_baostock(qlib_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """通过 baostock 拉取单个指数历史K线。

    指数请求字段仅取 OHLCV+amount（指数无 isST/估值/换手率字段）。

    Args:
        qlib_code: qlib 代码格式 'sh000001'
        start_date/end_date: 'YYYY-MM-DD'
    Returns:
        DataFrame: date,open,high,low,close,volume,amount（数值列已转 float）
    Raises:
        RuntimeError: baostock 调用失败
    """
    import baostock as bs
    from app.services.data.baostock_client import to_baostock_code, _ensure_login

    bs_code = to_baostock_code(qlib_code)  # sh000001 -> sh.000001
    _ensure_login()
    rs = bs.query_history_k_data_plus(
        code=bs_code,
        fields="date,code,open,high,low,close,volume,amount",
        start_date=start_date, end_date=end_date,
        frequency="d",
    )
    if rs.error_code != '0':
        raise RuntimeError(
            f"query_history_k_data_plus failed for {bs_code}: {rs.error_code} {rs.error_msg}"
        )
    data_list = []
    while (rs.error_code == '0') and rs.next():
        data_list.append(rs.get_row_data())
    df = pd.DataFrame(data_list, columns=rs.fields)
    # 数值列转 float
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = df["date"].astype(str)
    return df


def sync_indices_to_qlib(provider_uri: str, indices: list = None, days: int = 365) -> dict:
    """通过 baostock 同步指数到 qlib bin。

    指数清单由 config.quant.sync_indices 配置，默认含 8 大指数。
    指数同步不扩展日历（chenditc 日历已完整），仅按现有日历对齐写入。

    Args:
        provider_uri: qlib 数据目录
        indices: qlib 代码列表（如 ['sh000001', 'sh000300']），None 时读 config
        days: 拉取最近 N 天数据（仅用于日志提示，实际拉取从日历起始到今天）

    Returns:
        dict: {ok, success, failed, indices, total, source}
    """
    cal_path = os.path.join(provider_uri, "calendars", "day.txt")
    if not os.path.exists(cal_path):
        return {"ok": False, "error": "日历文件不存在"}

    calendar = _get_calendar(provider_uri)
    if not calendar:
        return {"ok": False, "error": "日历为空"}

    cal_set = set(calendar)
    cal_index = {d: i for i, d in enumerate(calendar)}

    index_list = _get_index_list(indices)
    success = 0
    failed = 0
    indices_synced = []

    # 拉取日期范围：从日历起始到今天
    start_date = calendar[0]
    end_date = datetime.now().strftime("%Y-%m-%d")

    for qlib_code in index_list:
        try:
            df = _fetch_index_via_baostock(qlib_code, start_date, end_date)
            if df is None or df.empty:
                logger.warning("指数 %s 无数据", qlib_code)
                failed += 1
                continue

            # 只保留日历中的日期（不扩展日历）
            df = df[df["date"].isin(cal_set)]
            if df.empty:
                logger.warning("指数 %s 过滤后无数据", qlib_code)
                failed += 1
                continue

            # 写入 bin 文件
            feat_dir = os.path.join(provider_uri, "features", qlib_code.lower())
            os.makedirs(feat_dir, exist_ok=True)

            for field in INDEX_FIELDS:
                if field not in df.columns:
                    continue
                bin_path = os.path.join(feat_dir, f"{field}.day.bin")
                # 构建完整数组，按日历索引填充
                values = np.full(len(calendar), np.nan, dtype=np.float32)
                for d, val in zip(df["date"].tolist(), df[field].tolist()):
                    if d in cal_index and val is not None and not pd.isna(val):
                        values[cal_index[d]] = float(val)
                _write_bin(bin_path, values, 0)

            logger.info("指数 %s 同步完成: %d 条数据", qlib_code, len(df))
            success += 1
            indices_synced.append(qlib_code)

        except Exception as e:
            logger.error("指数 %s 同步失败: %s", qlib_code, e)
            failed += 1

    logger.info("指数同步完成(baostock): 成功%d, 失败%d, 共%d", success, failed, len(index_list))
    return {
        "ok": True,
        "success": success,
        "failed": failed,
        "indices": indices_synced,
        "total": len(index_list),
        "source": "baostock",
    }
