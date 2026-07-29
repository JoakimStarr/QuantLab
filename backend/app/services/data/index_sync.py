"""指数行情同步到 qlib bin 格式

通过 akshare 的 stock_zh_index_daily 接口拉取指数日K数据，
转换为 qlib bin 格式写入 features 目录。

支持指数：上证指数、沪深300、上证50、中证500、中证1000、
深证成指、创业板指、科创50。
"""
import os
import logging
import numpy as np

from app.services.data.eod_incremental import (
    _write_bin,
    _get_calendar,
    _write_calendar,
)

logger = logging.getLogger(__name__)

INDEX_LIST = [
    ("sh000001", "上证指数"),
    ("sh000300", "沪深300"),
    ("sh000016", "上证50"),
    ("sh000905", "中证500"),
    ("sh000852", "中证1000"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
    ("sh000688", "科创50"),
]

INDEX_FIELDS = ["open", "high", "low", "close", "volume"]


def sync_indices_to_qlib(provider_uri, days=365):
    """同步指数数据到 qlib bin

    通过 akshare 拉取指数日K行情，写入 qlib bin 格式。
    如遇日历中不存在的新日期，自动扩展日历。

    Args:
        provider_uri: qlib 数据目录
        days: 拉取最近 N 天的数据（用于日志提示，akshare 接口返回全量历史）

    Returns:
        dict: 同步结果，包含 ok/success/failed/indices
    """
    import akshare as ak

    cal_path = os.path.join(provider_uri, "calendars", "day.txt")
    if not os.path.exists(cal_path):
        return {"ok": False, "error": "日历文件不存在"}

    calendar = _get_calendar(provider_uri)
    if not calendar:
        return {"ok": False, "error": "日历为空"}

    cal_set = set(calendar)
    cal_index = {d: i for i, d in enumerate(calendar)}

    success = 0
    failed = 0
    indices_synced = []

    for qlib_code, name in INDEX_LIST:
        try:
            df = ak.stock_zh_index_daily(symbol=qlib_code)
            if df is None or df.empty:
                logger.warning("指数 %s 无数据", qlib_code)
                failed += 1
                continue

            # 确保日期列格式统一 (YYYY-MM-DD)
            df["date"] = df["date"].astype(str).str[:10]

            # 只扩展日历中不存在的、且在现有日历起始日期之后的日期
            # 不扩展早于现有日历起始日期的历史日期（避免个股bin长度不匹配）
            cal_first = calendar[0] if calendar else None
            new_dates = sorted(d for d in set(df["date"].tolist()) - cal_set
                              if cal_first is None or d >= cal_first)
            if new_dates:
                calendar = sorted(set(calendar + new_dates))
                cal_index = {d: i for i, d in enumerate(calendar)}
                cal_set = set(calendar)
                _write_calendar(provider_uri, calendar)
                logger.info("日历扩展: 新增 %d 个日期", len(new_dates))

            # 只保留日历中的日期
            df = df[df["date"].isin(cal_set)]

            # 写入 bin 文件
            feat_dir = os.path.join(provider_uri, "features", qlib_code)
            os.makedirs(feat_dir, exist_ok=True)

            for field in INDEX_FIELDS:
                if field not in df.columns:
                    continue
                bin_path = os.path.join(feat_dir, f"{field}.day.bin")

                # 构建完整数组，按日历索引填充
                values = np.full(len(calendar), np.nan, dtype=np.float32)
                for d, val in zip(df["date"].tolist(), df[field].tolist()):
                    if d in cal_index and val is not None:
                        v = float(val)
                        if not np.isnan(v):
                            values[cal_index[d]] = v

                _write_bin(bin_path, values, 0)

            logger.info("指数 %s(%s) 同步完成: %d 条数据",
                        qlib_code, name, len(df))
            success += 1
            indices_synced.append(f"{qlib_code}({name})")

        except Exception as e:
            logger.error("指数 %s 同步失败: %s", qlib_code, e)
            failed += 1

    return {
        "ok": True,
        "success": success,
        "failed": failed,
        "indices": indices_synced,
        "total": len(INDEX_LIST),
    }
