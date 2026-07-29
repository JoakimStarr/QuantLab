"""增量EOD数据同步（基于akshare，国内源访问快）

通过 akshare 的 stock_zh_a_hist 接口拉取个股日K数据（OHLCV），
转换为 qlib bin 格式追加/覆盖到现有 qlib_bin 目录，并更新日历。

qlib bin 文件格式（通过实际数据验证）：
  - 头部：4 字节，struct.pack("<f", start_index)，start_index 以 float32 存储
  - 数据：float32 数组（小端），紧跟头部之后
  - 文件大小 = 4 + 4 * N（N = 数据点数 = 日历长度）
  - 路径：{provider_uri}/features/{instrument_lower}/{field}.day.bin
"""
import os
import struct
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

QLIB_BIN_DTYPE = "<f4"  # 小端 float32
QLIB_BIN_HEADER_FMT = "<f"  # start_index 以 float32 存储
QLIB_BIN_HEADER_SIZE = 4  # 仅 4 字节（非 20 字节）

# 同步的字段列表（akshare列名 -> qlib字段名）
FIELD_MAP = {
    "开盘": "open",
    "最高": "high",
    "最低": "low",
    "收盘": "close",
    "成交量": "volume",
}

# 需要复权对齐的价格类字段（volume/amount 不做复权）
PRICE_FIELDS = {"open", "high", "low", "close", "vwap", "adjclose"}


def _read_bin(file_path: str):
    """读取 qlib bin 文件，返回 (values, start_index)

    Args:
        file_path: .day.bin 文件路径

    Returns:
        (np.ndarray[float32], int): 数据数组和 start_index。
        文件不存在时返回 (None, 0)。
    """
    if not os.path.exists(file_path):
        return None, 0
    try:
        with open(file_path, "rb") as f:
            hdr = f.read(QLIB_BIN_HEADER_SIZE)
            if len(hdr) < QLIB_BIN_HEADER_SIZE:
                return None, 0
            start_index = int(round(struct.unpack(QLIB_BIN_HEADER_FMT, hdr)[0]))
            data = np.fromfile(f, dtype=QLIB_BIN_DTYPE)
        return data, start_index
    except Exception as e:
        logger.warning("读取 bin 文件失败 %s: %s", file_path, e)
        return None, 0


def _write_bin(file_path: str, values: np.ndarray, start_index: int):
    """写入 qlib bin 文件

    Args:
        file_path: .day.bin 文件路径
        values: float32 数据数组
        start_index: 数据在日历中的起始索引
    """
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "wb") as f:
        # 写头部：start_index 以 float32 存储（4 字节）
        f.write(struct.pack(QLIB_BIN_HEADER_FMT, float(start_index)))
        # 写数据
        values.astype(QLIB_BIN_DTYPE).tofile(f)


def _get_calendar(provider_uri: str):
    """读取 qlib 日历

    Returns:
        list[str]: 日期列表（YYYY-MM-DD 格式），按时间升序
    """
    cal_path = os.path.join(provider_uri, "calendars", "day.txt")
    if not os.path.exists(cal_path):
        return []
    with open(cal_path, "r") as f:
        return [line.strip() for line in f if line.strip()]


def _write_calendar(provider_uri: str, dates: list):
    """写入 qlib 日历（全量覆盖）"""
    cal_path = os.path.join(provider_uri, "calendars", "day.txt")
    os.makedirs(os.path.dirname(cal_path), exist_ok=True)
    with open(cal_path, "w") as f:
        for d in dates:
            f.write(d + "\n")


def _read_instruments(provider_uri: str, universe: str):
    """读取股票池文件，返回 qlib 代码列表

    instruments 文件格式（qlib 标准）：每行 `SH600000\t2005-04-08\t2005-06-30`，
    表示某股票在 [start, end] 期间是 universe 的成分股。同一股票会因成分股
    调整出现多行，需按最大 end_date 过滤当前活跃成分股并去重。
    """
    if universe == "all":
        pool_file = os.path.join(provider_uri, "instruments", "all.txt")
    else:
        pool_file = os.path.join(provider_uri, "instruments", f"{universe}.txt")
    if not os.path.exists(pool_file):
        return []

    rows = []  # [(code, end_date), ...]
    with open(pool_file, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            code = parts[0].strip()
            if not code:
                continue
            end_date = parts[2].strip() if len(parts) >= 3 else None
            rows.append((code, end_date))

    if not rows:
        return []

    # 若含日期字段，仅取最新一期调整后的成分股（end_date == max_end_date）
    end_dates = [e for _, e in rows if e]
    if end_dates:
        max_end = max(end_dates)
        rows = [(c, e) for c, e in rows if e == max_end]

    # 去重，保留首次出现顺序
    seen = set()
    codes = []
    for c, _ in rows:
        if c not in seen:
            seen.add(c)
            codes.append(c)
    return codes
def _qlib_code_to_akshare(qlib_code: str):
    """qlib 代码转 akshare 代码

    SH600000 -> 600000, SZ000001 -> 000001, BJ430017 -> 430017
    """
    c = qlib_code.upper()
    if c.startswith(("SH", "SZ", "BJ")):
        return c[2:]
    return c


def _merge_calendar(old_dates: list, new_dates: list):
    """合并新旧日历，返回排序去重后的日期列表"""
    return sorted(set(old_dates + new_dates))


def _build_index_mapping(old_dates: list, old_start: int, old_len: int,
                         merged_dates: list):
    """构建旧数据到合并后日历的索引映射

    Args:
        old_dates: 旧日历（完整列表）
        old_start: 旧 bin 数据的 start_index
        old_len: 旧 bin 数据长度
        merged_dates: 合并后的日历

    Returns:
        np.ndarray[int64]: 长度为 old_len 的数组，
        每个元素是该位置旧数据在 merged_dates 中的索引，-1 表示无法映射
    """
    merged_idx = {d: i for i, d in enumerate(merged_dates)}
    mapping = np.full(old_len, -1, dtype=np.int64)
    for j in range(old_len):
        cal_pos = old_start + j
        if 0 <= cal_pos < len(old_dates):
            d = old_dates[cal_pos]
            if d in merged_idx:
                mapping[j] = merged_idx[d]
    return mapping


def _fetch_eod_akshare(qlib_code: str, start_str: str, end_str: str):
    """同步调用 akshare 拉取日K数据（在线程池中执行）

    优先使用新浪源（stock_zh_a_daily，反爬风险低），失败时回退到东财源
    （stock_zh_a_hist）。新浪源 symbol 格式为 sh600000（qlib code 小写），
    东财源 symbol 格式为 600000（纯数字）。

    Returns:
        pd.DataFrame: 列包含 date, open, high, low, close, volume；失败返回 None
    """
    import akshare as ak

    ak_code = _qlib_code_to_akshare(qlib_code)
    sina_symbol = qlib_code.lower()  # sh600000
    keep_cols = ["date"] + list(FIELD_MAP.values())
    df = None

    # 方式1：新浪源（反爬风险低，含 volume）
    try:
        df = ak.stock_zh_a_daily(
            symbol=sina_symbol, start_date=start_str, end_date=end_str, adjust="qfq",
        )
        if df is not None and not df.empty:
            # 新浪源列名已是英文：date/open/high/low/close/volume/...
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            keep = [c for c in keep_cols if c in df.columns]
            df = df[keep]
        else:
            df = None
    except Exception as e:
        logger.debug("新浪源拉取 %s 失败: %s", qlib_code, e)
        df = None

    # 方式2：回退到东财源
    if df is None or df.empty:
        try:
            df = ak.stock_zh_a_hist(
                symbol=ak_code, period="daily",
                start_date=start_str, end_date=end_str, adjust="qfq",
            )
            if df is not None and not df.empty:
                rename_map = {"日期": "date"}
                rename_map.update(FIELD_MAP)
                df = df.rename(columns=rename_map)
                df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
                keep = [c for c in keep_cols if c in df.columns]
                df = df[keep]
            else:
                df = None
        except Exception as e:
            logger.debug("东财源拉取 %s 失败: %s", qlib_code, e)
            df = None

    if df is None or df.empty:
        return None
    return df


async def incremental_sync_eod(
    universe: str = "csi300",
    days: int = 5,
    provider_uri: str = None,
    overwrite: bool = False,
) -> dict:
    """增量同步 EOD 数据（基于 akshare，国内源）

    拉取最近 N 天的日K数据，转换为 qlib bin 格式追加到现有目录。

    默认仅追加日历中不存在的新日期（overwrite=False），避免因 akshare qfq
    与 chenditc 复权方式不同导致已有价格序列被覆盖。如需强制覆盖已有日期
    （例如修复缺失数据），可设 overwrite=True。

    Args:
        universe: 股票池（csi300/csi500/all）
        days: 同步最近 N 天数据（1-30）
        provider_uri: qlib 数据目录，默认从 settings 读取
        overwrite: 是否覆盖日历中已有的日期数据（默认 False，仅追加新日期）

    Returns:
        dict: 同步结果，包含 ok/success/failed/new_dates 等
    """
    import asyncio
    from functools import partial
    from app.core.config import settings

    if provider_uri is None:
        provider_uri = settings.qlib_provider_path

    if not provider_uri or not os.path.exists(provider_uri):
        return {"ok": False, "error": f"qlib数据目录不存在: {provider_uri}"}

    logger.info("开始增量EOD同步: universe=%s, days=%d, dir=%s", universe, days, provider_uri)

    # 读取股票池
    codes = _read_instruments(provider_uri, universe)
    if not codes:
        return {"ok": False, "error": f"股票池为空或文件不存在: {universe}"}

    # 日期范围（多拉几天确保覆盖周末/节假日）
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days + 15)
    start_str = start_date.strftime("%Y%m%d")
    end_str = end_date.strftime("%Y%m%d")

    # 读取现有日历
    old_calendar = _get_calendar(provider_uri)
    cal_set = set(old_calendar) if old_calendar else set()

    # 日期范围检查：只处理日历最后一天之后或附近的数据
    loop = asyncio.get_running_loop()

    success_count = 0
    fail_count = 0
    skip_count = 0
    all_new_dates = set()

    for i, qlib_code in enumerate(codes):
        try:
            # 在线程池中同步调用 akshare（内部优先新浪源，回退东财源）
            fn = partial(_fetch_eod_akshare, qlib_code, start_str, end_str)
            df = await asyncio.wait_for(loop.run_in_executor(None, fn), timeout=30)

            if df is None or df.empty:
                skip_count += 1
                continue

            # 收集新日期
            for d in df["date"].tolist():
                if d not in cal_set:
                    all_new_dates.add(d)

            # 为该股票写入各字段的 bin 文件
            feat_dir = os.path.join(provider_uri, "features", qlib_code.lower())
            _sync_stock_bin(
                feat_dir, df, old_calendar, FIELD_MAP.values(), overwrite,
            )
            success_count += 1

        except asyncio.TimeoutError:
            logger.debug("拉取 %s 超时", qlib_code)
            fail_count += 1
        except Exception as e:
            logger.debug("拉取 %s 失败: %s", qlib_code, e)
            fail_count += 1

        # 进度日志
        if (i + 1) % 100 == 0:
            logger.info("EOD同步进度: %d/%d (成功%d, 失败%d, 跳过%d)",
                        i + 1, len(codes), success_count, fail_count, skip_count)

    # 更新日历（合并新日期）
    new_dates_sorted = sorted(all_new_dates)
    if new_dates_sorted:
        merged_cal = _merge_calendar(old_calendar, new_dates_sorted)
        _write_calendar(provider_uri, merged_cal)
        logger.info("日历更新: %d -> %d (新增 %d 个交易日)",
                    len(old_calendar), len(merged_cal), len(new_dates_sorted))

    logger.info("EOD增量同步完成: 成功%d, 失败%d, 跳过%d, 新增日期%d",
                success_count, fail_count, skip_count, len(new_dates_sorted))

    return {
        "ok": True,
        "universe": universe,
        "days": days,
        "total_stocks": len(codes),
        "success": success_count,
        "failed": fail_count,
        "skipped": skip_count,
        "new_dates": new_dates_sorted,
        "calendar_before": len(old_calendar),
        "calendar_after": len(old_calendar) + len(new_dates_sorted),
    }


def _sync_stock_bin(feat_dir: str, df: pd.DataFrame,
                    old_calendar: list, fields, overwrite: bool = False):
    """将单只股票的 akshare 数据同步到 bin 文件

    策略：
      1. 将 df 的日期与 old_calendar 合并，得到 merged_calendar
      2. 对每个字段，读取旧 bin，按日期映射重建数组
      3. 当 overwrite=True 时用新数据覆盖所有匹配日期；否则仅写入日历中
         不存在的新日期（避免不同复权方式导致价格序列断裂）
      4. 写回 bin 文件

    注意：merged_calendar 仅用于确定新日期的索引位置。全局日历合并
    由调用方在所有股票处理完成后统一执行。
    """
    if not old_calendar:
        # 日历为空，无法定位索引（极端情况）
        return

    cal_set = set(old_calendar)

    # 分离已有日期和新日期
    df_dates = df["date"].tolist()
    new_dates_in_df = sorted([d for d in df_dates if d not in cal_set])

    # 合并后的日历（仅用于确定新日期的索引）
    merged_cal = _merge_calendar(old_calendar, new_dates_in_df)
    merged_idx = {d: i for i, d in enumerate(merged_cal)}

    # 筛选需要写入的日期：overwrite=True 时全部写入，否则仅写入新日期
    if overwrite:
        write_pairs = list(zip(df_dates, range(len(df_dates))))
    else:
        write_pairs = [(d, i) for i, d in enumerate(df_dates) if d not in cal_set]

    if not write_pairs and new_dates_in_df:
        # new_dates_in_df 非空但 write_pairs 为空（理论上不会发生）
        write_pairs = [(d, df_dates.index(d)) for d in new_dates_in_df]

    for field in fields:
        if field not in df.columns:
            continue
        bin_path = os.path.join(feat_dir, f"{field}.day.bin")

        # 读取旧数据
        old_values, old_start = _read_bin(bin_path)

        # 准备新数据
        new_values = df[field].values.astype(np.float32)

        if old_values is None or len(old_values) == 0:
            # 无旧数据：创建新数组，仅填充需要写入的日期
            arr = np.full(len(merged_cal), np.nan, dtype=np.float32)
            for d, row_i in write_pairs:
                if d in merged_idx:
                    arr[merged_idx[d]] = new_values[row_i]
            _write_bin(bin_path, arr, 0)
        else:
            # 有旧数据：按日期映射重建
            mapping = _build_index_mapping(
                old_calendar, old_start, len(old_values), merged_cal,
            )
            arr = np.full(len(merged_cal), np.nan, dtype=np.float32)
            # 散布旧数据（保留已有值）
            valid = mapping >= 0
            if valid.any():
                arr[mapping[valid]] = old_values[valid]

            # ===== 复权基准对齐 =====
            # chenditc 历史数据首值归一化为 1.0，akshare 新数据是实际前复权价格，
            # 两者直接拼接会导致价格跳变。通过旧数据最后一个有效值与 akshare 同日值
            # 计算复权比例，将 akshare 新数据乘以比例后再写入。
            if field in PRICE_FIELDS and not overwrite:
                old_valid_indices = np.where(~np.isnan(old_values))[0]
                if len(old_valid_indices) > 0:
                    old_last_idx = old_valid_indices[-1]
                    old_last_value = float(old_values[old_last_idx])
                    cal_pos = old_start + old_last_idx
                    if 0 <= cal_pos < len(old_calendar):
                        old_last_date = old_calendar[cal_pos]
                        if old_last_date in df_dates:
                            matching_idx = df_dates.index(old_last_date)
                            akshare_value = float(new_values[matching_idx])
                            if (not np.isnan(akshare_value)
                                    and abs(akshare_value) > 1e-12):
                                ratio = old_last_value / akshare_value
                                new_values = new_values * ratio
                                logger.debug(
                                    "复权对齐 %s/%s: ratio=%.6f "
                                    "(old=%.4f, akshare=%.4f, date=%s)",
                                    os.path.basename(feat_dir), field, ratio,
                                    old_last_value, akshare_value, old_last_date,
                                )

            # 写入新数据（仅指定日期）
            for d, row_i in write_pairs:
                if d in merged_idx:
                    arr[merged_idx[d]] = new_values[row_i]
            _write_bin(bin_path, arr, 0)
