"""增量EOD数据同步（基于akshare，国内源访问快）

通过 akshare 的 stock_zh_a_hist 接口拉取个股日K数据（OHLCV），
转换为 qlib bin 格式追加/覆盖到现有 qlib_bin 目录，并更新日历。

qlib bin 文件格式（通过实际数据验证）：
  - 头部：4 字节，struct.pack("<f", start_index)，start_index 以 float32 存储
  - 数据：float32 数组（小端），紧跟头部之后
  - 文件大小 = 4 + 4 * N（N = 数据点数 = 日历长度）
  - 路径：{provider_uri}/features/{instrument_lower}/{field}.day.bin
"""
import logging
import os
import struct
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from app.services.data.data_adjusted import apply_hfq_transform, validate_change_consistency
from app.services.data.data_clean import format_date_series

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


def _get_limit_pct(qlib_code: str) -> float:
    """根据代码前缀返回涨跌停比例（不含ST股的5%判定，ST状态需额外获取）。

    主板(60/00): 10%, 科创(688)/创业(300/301): 20%, 北交所(83/87/43/92/88): 30%
    """
    c = qlib_code.upper()
    num = c[2:] if c.startswith(("SH", "SZ", "BJ")) else c
    if num.startswith("688") or num.startswith(("300", "301")):
        return 0.20
    if num.startswith(("83", "87", "43", "92", "88")):
        return 0.30
    return 0.10


def _compute_tradable(close: pd.Series, pct_change: pd.Series,
                      code: str = None, is_st: pd.Series = None) -> pd.Series:
    """计算可交易 mask：触及涨跌停日标记为 0.0，正常为 1.0。

    涨跌幅阈值按板块区分（主板10%/科创创业20%/北交所30%）；若提供 is_st 标记，
    ST 股按 5% 判定（修复 ST 股触及5%涨跌停仍被判为可交易的 bug）。

    Args:
        close: 收盘价 Series（提供索引对齐基准）
        pct_change: 涨跌幅 Series，单位为百分比（如 2.0 表示 2%）
        code: qlib 代码（如 sz000001），用于判断板块涨跌停比例；None 时按主板10%
        is_st: 是否 ST 的布尔 Series（baostock 提供），如有则 ST 日用 5% 阈值；
            为 None 时按板块阈值判定（akshare fallback 路径，向后兼容）

    Returns:
        pd.Series[float]: 1.0=可交易，0.0=涨跌停不可交易
    """
    # _get_limit_pct 返回分数（0.10/0.20/0.30），统一转换为百分比（10.0/20.0/30.0）
    base_pct = _get_limit_pct(code) * 100.0
    threshold = pd.Series(base_pct, index=close.index, dtype=float)

    # ST 股按 5% 判定（仅 is_st 提供时生效，akshare 路径 is_st=None 不受影响）
    if is_st is not None:
        st_mask = is_st.astype(bool).reindex(close.index, fill_value=False)
        threshold[st_mask] = 5.0

    # 涨跌幅绝对值 >= 阈值 - 容错 视为触及涨跌停（减 0.01 容忍浮点误差）
    pct_aligned = pct_change.reindex(close.index)
    hit = pct_aligned.abs() >= (threshold - 0.01)
    return pd.Series(np.where(hit, 0.0, 1.0), index=close.index, dtype=float)


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


def _read_bin_meta(file_path: str) -> tuple:
    """只读 bin 头与长度（不读整个文件），返回 (start_index, 数据点数)。

    热路径（追加/定点覆盖）里判断 bin 是否与日历对齐时，避免全文件读。
    文件缺失/损坏时返回 (None, 0)。
    """
    try:
        size = os.path.getsize(file_path)
        with open(file_path, "rb") as f:
            hdr = f.read(QLIB_BIN_HEADER_SIZE)
    except OSError:
        # 文件缺失是常态（标的无该字段/未上市），静默；其他 OSError 少见，一并跳过
        return None, 0
    except Exception as e:
        logger.warning("读取 bin 元信息失败（未知错误）path=%s: %s", file_path, e)
        return None, 0
    if len(hdr) < QLIB_BIN_HEADER_SIZE:
        logger.warning("bin 头损坏（长度不足）path=%s size=%s", file_path, size)
        return None, 0
    n = (size - QLIB_BIN_HEADER_SIZE) // 4
    if n < 0:
        logger.warning("bin 头损坏（数据区长为负）path=%s size=%s", file_path, size)
        return None, 0
    try:
        start_index = int(round(struct.unpack(QLIB_BIN_HEADER_FMT, hdr)[0]))
    except Exception as e:
        logger.warning("bin 头损坏（start_index 解析失败）path=%s: %s", file_path, e)
        return None, 0
    return start_index, n


# 已确保存在的 features/* 目录集合：避免热路径每文件一次 os.makedirs syscall
_written_dirs: set = set()


def _read_last_valid(bin_path: str):
    """读取 bin 最后一个有限值（用于取已存 factor / 上一根原始收盘）；无则 None。"""
    values, _start = _read_bin(bin_path)
    if values is None or len(values) == 0:
        return None
    mask = np.isfinite(values)
    if not mask.any():
        return None
    return float(values[int(np.nonzero(mask)[0][-1])])


def _load_incremental_base(feat_dir: str):
    """读取已存 bin 的后复权基准：返回 (base_factor|None, prev_raw_close|None)。

    base_factor = 已存 factor 的最后一个有限值（缺失/非法 → None，退化为全量模式）；
    prev_raw_close = 最后一根已存原始收盘 = hfq_close / factor（用于新 bar 的边界 e0）。
    """
    base = _read_last_valid(os.path.join(feat_dir, "factor.day.bin"))
    last_close = _read_last_valid(os.path.join(feat_dir, "close.day.bin"))
    if base is None or not np.isfinite(base) or base <= 0:
        return None, last_close
    prev_raw = (last_close / base) if last_close is not None else None
    return float(base), prev_raw


def _ensure_dir(dir_path: str) -> None:
    if dir_path not in _written_dirs:
        os.makedirs(dir_path, exist_ok=True)
        _written_dirs.add(dir_path)


def _write_bin(file_path: str, values: np.ndarray, start_index: int):
    """写入 qlib bin 文件（原子：先写临时文件再 os.replace）。

    原子写保证并发读安全：回测/挖掘在同步写 bin 期间读取时，只会看到
    完整的旧文件或完整的新文件，绝不会读到写了一半（截断）的文件，
    这是"数据同步与回测解耦、各干各的"的前提。

    Args:
        file_path: .day.bin 文件路径
        values: float32 数据数组
        start_index: 数据在日历中的起始索引
    """
    _ensure_dir(os.path.dirname(file_path))
    tmp_path = file_path + ".tmp"
    with open(tmp_path, "wb") as f:
        # 写头部：start_index 以 float32 存储（4 字节）
        f.write(struct.pack(QLIB_BIN_HEADER_FMT, float(start_index)))
        # 写数据
        values.astype(QLIB_BIN_DTYPE).tofile(f)
    os.replace(tmp_path, file_path)


def _write_bin_append(file_path: str, tail_values: np.ndarray) -> None:
    """文件尾追加 float32 数据（日历尾部扩展的 fast path）。

    前置条件（由调用方保证）：旧 bin 头部+旧数据已对齐旧日历，追加后
    文件长度 = 头部 + (旧点数 + len(tail_values)) × 4，与扩展后的新日历一致。
    仅追加数据区，不改头部与既有数据。
    """
    _ensure_dir(os.path.dirname(file_path))
    with open(file_path, "ab") as f:
        np.asarray(tail_values, dtype=QLIB_BIN_DTYPE).tofile(f)


def _write_bin_positions(file_path: str, positions: list, values) -> None:
    """在已存在（长度==目标日历）的 bin 上定点覆盖若干位置的值。

    位置 index 语义：arr[i] 对齐 global_calendar[i]，字节偏移 = 4 + i × 4。
    用于回填后续批次（同长窗口内补写更早日期的值）与 EOD 补停牌股缺口，
    避免把整段历史读出再全量写回。
    """
    _ensure_dir(os.path.dirname(file_path))
    vals = np.asarray(list(values), dtype=QLIB_BIN_DTYPE)
    with open(file_path, "r+b") as f:
        for pos, val in zip(positions, vals, strict=False):
            f.seek(QLIB_BIN_HEADER_SIZE + int(pos) * 4)
            f.write(struct.pack("<f", float(val)))


def _pad_bins_to_calendar(qlib_dir: str, calendar: list) -> int:
    """日历扩展后，把所有比新日历短的 bin 文件补齐到新长度（末尾补 NaN）。

    新增交易日时（如"今天"首次入库），没有该日数据的股票（退市/长期停牌）的
    bin 不会被重新写入，长度停留在旧日历 → 数据校验报"长度异常"。本函数扫描
    features/*/{field}.day.bin，短于目标长度的统一末尾补 NaN 扩展（覆盖股票
    OHLCV / 宏观 / 财报 / 指数等全部字段）；长度超长的（异常）仅记 warning。

    线程池并行处理：全市场 55 万+ 个 bin 逐个串行校验/补齐耗时 10 分钟级，
    文件互相独立且 _write_bin 原子写，并行安全（8 线程约快 4-6 倍）。
    """
    feat_root = os.path.join(qlib_dir, "features")
    if not os.path.isdir(feat_root):
        return 0
    target_size = QLIB_BIN_HEADER_SIZE + 4 * len(calendar)
    target_n = len(calendar)

    # 先收集全部 bin 路径（listdir 一次），再并行处理
    all_bins: list[str] = []
    for code in os.listdir(feat_root):
        code_dir = os.path.join(feat_root, code)
        if not os.path.isdir(code_dir):
            continue
        for fname in os.listdir(code_dir):
            if fname.endswith(".day.bin"):
                all_bins.append(os.path.join(code_dir, fname))
    if not all_bins:
        return 0

    def _pad_one(path: str) -> int:
        try:
            size = os.path.getsize(path)
        except OSError as e:
            logger.warning("补齐 bin 跳过（stat 失败）path=%s: %s", path, e)
            return 0
        if size == target_size:
            return 0
        if size < target_size:
            raw = np.fromfile(path, dtype="<f4")
            arr = np.full(target_n, np.nan, dtype="<f4")
            if raw.size > 1:
                keep = min(raw.size - 1, target_n)
                arr[:keep] = raw[1 : keep + 1]
            _write_bin(path, arr, 0)
            return 1
        logger.warning("bin 长度超过日历（异常）: %s %d > %d", path, size, target_size)
        return 0

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=8) as pool:
        padded = sum(pool.map(_pad_one, all_bins))
    if padded:
        logger.info("已按新日历补齐 %d 个 bin 文件（末尾补 NaN，共扫描 %d 个）", padded, len(all_bins))
    return padded


def _get_calendar(provider_uri: str):
    """读取 qlib 日历

    Returns:
        list[str]: 日期列表（YYYY-MM-DD 格式），按时间升序
    """
    cal_path = os.path.join(provider_uri, "calendars", "day.txt")
    if not os.path.exists(cal_path):
        return []
    with open(cal_path) as f:
        return [line.strip() for line in f if line.strip()]


def _write_calendar(provider_uri: str, dates: list):
    """写入 qlib 日历（全量覆盖，原子写：避免并发读者读到写了一半的 day.txt）"""
    cal_path = os.path.join(provider_uri, "calendars", "day.txt")
    os.makedirs(os.path.dirname(cal_path), exist_ok=True)
    tmp_path = cal_path + ".tmp"
    with open(tmp_path, "w") as f:
        for d in dates:
            f.write(d + "\n")
    os.replace(tmp_path, cal_path)


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
    with open(pool_file) as f:
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

    优先使用东财源（stock_zh_a_hist，含涨跌幅字段，用于计算tradable mask），
    失败时回退到新浪源（stock_zh_a_daily，反爬风险低但无涨跌幅）。

    Returns:
        pd.DataFrame: 列含 date/open/high/low/close/volume，可选 pct_change；
        失败返回 None
    """
    import akshare as ak

    ak_code = _qlib_code_to_akshare(qlib_code)
    sina_symbol = qlib_code.lower()
    keep_cols = ["date"] + list(FIELD_MAP.values()) + ["pct_change"]
    df = None

    # 方式1：东财源（含涨跌幅，用于tradable计算）
    try:
        df = ak.stock_zh_a_hist(
            symbol=ak_code, period="daily",
            start_date=start_str, end_date=end_str, adjust="qfq",
        )
        if df is not None and not df.empty:
            rename_map = {"日期": "date", "涨跌幅": "pct_change"}
            rename_map.update(FIELD_MAP)
            df = df.rename(columns=rename_map)
            df["date"] = format_date_series(df["date"])
            keep = [c for c in keep_cols if c in df.columns]
            df = df[keep]
        else:
            df = None
    except Exception as e:
        logger.debug("东财源拉取 %s 失败: %s", qlib_code, e)
        df = None

    # 方式2：回退到新浪源（无涨跌幅，tradable走近似分支）
    if df is None or df.empty:
        try:
            df = ak.stock_zh_a_daily(
                symbol=sina_symbol, start_date=start_str, end_date=end_str, adjust="qfq",
            )
            if df is not None and not df.empty:
                df["date"] = format_date_series(df["date"])
                keep = [c for c in keep_cols if c in df.columns]
                df = df[keep]
            else:
                df = None
        except Exception as e:
            logger.debug("新浪源拉取 %s 失败: %s", qlib_code, e)
            df = None

    if df is None or df.empty:
        return None
    return df


def _gen_candidate_dates(start_date, end_date,
                         covered_dates, overwrite: bool) -> list:
    """生成候选同步日期（YYYY-MM-DD）。

    遍历 [start_date, end_date] 区间内的工作日；overwrite=False 时跳过 covered_dates。

    重要：covered_dates 必须是"确实已有数据的交易日"（来自 stock_daily），不能
    用 day.txt 日历——日历会被 padding 到当天（含尚未发布/未同步的日子），若以
    日历为准，则"日历已含但无数据"的当天会被误判为已同步而永不拉取（鸡生蛋：
    day.txt 领先 stock_daily）。非交易日（周末/节假日）由 baostock 返回空数据自然跳过。
    """
    cal_set = set(covered_dates) if covered_dates else set()
    dates = []
    cur = start_date
    while cur <= end_date:
        if cur.weekday() < 5:  # 仅工作日（周一至周五）
            d = cur.strftime("%Y-%m-%d")
            if overwrite or d not in cal_set:
                dates.append(d)
        cur += timedelta(days=1)
    return dates


async def _covered_trade_dates(start, end):
    """窗口内 stock_daily 实际已有数据的交易日集合（YYYY-MM-DD）。

    start/end 为 ``datetime.date``；返回 None 表示查询失败（调用方回退旧行为）。
    """
    try:
        from sqlalchemy import text

        from app.core.database import async_session
        async with async_session() as session:
            rows = (await session.execute(
                text("SELECT DISTINCT trade_date FROM stock_daily "
                     "WHERE trade_date >= :s AND trade_date <= :e"),
                {"s": start, "e": end},
            )).all()
        out = set()
        for r in rows:
            d = r[0]
            out.add(d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10])
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("查询 stock_daily 已有交易日失败，回退按日历判断: %s", e)
        return None



def _aggregate_baostock_days(dates: list, codes: list, old_calendar: list,
                             fetch_daily_all_a_stock_sync, from_baostock_code,
                             BaostockQuotaError) -> tuple:
    """逐日拉取 baostock 全市场并按股票聚合（候选日期收集/聚合段）。

    Args:
        fetch_daily_all_a_stock_sync/from_baostock_code/BaostockQuotaError:
            由调用方导入后传入（baostock_client 可用性已在入口校验）。

    Returns:
        (per_stock_rows, fetched_dates, all_new_dates)：
        qlib_code_lower -> list[DataFrame]、成功拉取日期列表、新日历日期集合
    """
    from app.services.data.sync_progress import update_progress as _up

    cal_set = set(old_calendar) if old_calendar else set()
    codes_set = set(c.lower() for c in codes)

    # 按股票聚合各日数据：qlib_code_lower -> list[DataFrame]
    per_stock_rows = {}
    all_new_dates = set()
    fetched_dates = []

    total_dates = len(dates) if dates else 1
    for date_idx, date in enumerate(dates):
        try:
            df_all = fetch_daily_all_a_stock_sync(date)
        except BaostockQuotaError as e:
            # 当日请求配额耗尽，中止增量同步，避免逐日无谓重试
            logger.error("baostock 增量同步中止: %s", e)
            break
        except Exception as e:
            logger.warning("baostock 拉取 %s 失败: %s", date, e)
            continue
        if df_all is None or df_all.empty:
            # 非交易日/节假日返回空，自然跳过
            continue
        fetched_dates.append(date)
        if date not in cal_set:
            all_new_dates.add(date)
        _up(pct=10 + (date_idx + 1) / total_dates * 50,
            status="running", message=f"baostock 拉取 {date} ({date_idx + 1}/{total_dates})")

        # 代码转换：sh.600000 -> sh600000，并过滤到股票池
        df_all = df_all.copy()
        df_all["qlib_code"] = df_all["code"].apply(from_baostock_code)
        df_all["qlib_code_lower"] = df_all["qlib_code"].str.lower()
        df_all = df_all[df_all["qlib_code_lower"].isin(codes_set)]
        if df_all.empty:
            continue

        # 统一日期格式为 YYYY-MM-DD（与日历一致）
        df_all["date"] = format_date_series(df_all["date"])

        # 数值列转 float（baostock 可能返回字符串/对象类型）
        num_cols = ["open", "high", "low", "close", "preclose", "volume", "amount",
                    "pctChg", "isST"]
        for c in num_cols:
            if c in df_all.columns:
                df_all[c] = pd.to_numeric(df_all[c], errors="coerce")

        for qlib_code_lower, grp in df_all.groupby("qlib_code_lower"):
            per_stock_rows.setdefault(qlib_code_lower, []).append(grp)

    return per_stock_rows, fetched_dates, all_new_dates


def _write_baostock_bins(per_stock_rows: dict, provider_uri: str, old_calendar: list,
                         fields_to_write: list, overwrite: bool) -> tuple:
    """按股票写 bin（含后复权增量变换）并构建 stock_daily 落库记录。

    Returns:
        (success_count, fail_count, pg_rows)
    """
    from app.services.data.baostock_backfill import _f, _i  # 延迟导入避免循环依赖（backfill 模块级已 import 本模块）
    from app.services.data.sync_progress import update_progress as _up

    success_count = 0
    fail_count = 0
    total_stocks = len(per_stock_rows) if per_stock_rows else 1
    pg_rows = []  # stock_daily 落库记录：修复 EOD 只写 bin、repair 以 PG 为权威会丢 EOD 数据

    for stock_idx, (qlib_code_lower, grps) in enumerate(per_stock_rows.items()):
        try:
            df = pd.concat(grps, ignore_index=True)
            df = df.sort_values("date").reset_index(drop=True)
            qlib_code = qlib_code_lower.upper()

            # 构造写入 DataFrame（原始价；随后按后复权口径变换）
            out = pd.DataFrame({
                "date": df["date"].astype(str),
                "open": df["open"].astype(float),
                "high": df["high"].astype(float),
                "low": df["low"].astype(float),
                "close": df["close"].astype(float),
                "preclose": df["preclose"].astype(float) if "preclose" in df.columns else np.nan,
                "volume": df["volume"].astype(float),
                "pct_change": df["pctChg"].astype(float),
            })
            out["change"] = out["pct_change"] / 100.0
            # isST: baostock '1'=ST, '0'=非ST，转 bool 供 ST 5% 涨跌停判定
            if "isST" in df.columns:
                is_st = df["isST"].astype(str) == "1"
            else:
                is_st = None
            out["tradable"] = _compute_tradable(
                out["close"], out["pct_change"], code=qlib_code, is_st=is_st,
            )

            feat_dir = os.path.join(provider_uri, "features", qlib_code_lower)
            # 后复权增量：以已存 factor 为基准，新 bar 的 A = base_factor × 段内累计；
            # prev_raw_close 补上首根新增 bar 的边界除权因子（e0 = 前收/当日 preclose）。
            base_factor, prev_raw_close = _load_incremental_base(feat_dir)
            bad = validate_change_consistency(out["close"], out["preclose"], out["change"])
            if bad:
                logger.warning("%s: %d 根新增 bar 的 close/preclose 与 change 不一致（A_t 可能失真）",
                               qlib_code, len(bad))
            out = apply_hfq_transform(out, base_factor=base_factor, prev_raw_close=prev_raw_close)
            _sync_stock_bin(feat_dir, out, old_calendar, fields_to_write, overwrite)
            success_count += 1

            # 构建 stock_daily 全字段记录（ON CONFLICT DO NOTHING，重复写入幂等）
            for r in df.to_dict("records"):
                d = r["date"].strftime("%Y-%m-%d") if hasattr(r["date"], "strftime") else str(r["date"])[:10]
                pg_rows.append({
                    "code": qlib_code,
                    "trade_date": d,
                    "open": _f(r.get("open")), "high": _f(r.get("high")),
                    "low": _f(r.get("low")), "close": _f(r.get("close")),
                    "preclose": _f(r.get("preclose")), "volume": _f(r.get("volume")),
                    "amount": _f(r.get("amount")), "turn": _f(r.get("turn")),
                    "tradestatus": _i(r.get("tradestatus")), "pct_chg": _f(r.get("pctChg")),
                    "is_st": bool(r.get("isST")) if pd.notna(r.get("isST")) else None,
                    "pe_ttm": _f(r.get("peTTM")), "pb_mrq": _f(r.get("pbMRQ")),
                    "ps_ttm": _f(r.get("psTTM")), "pcf_ncf_ttm": _f(r.get("pcfNcfTTM")),
                    "adjustflag": _i(r.get("adjustflag")),
                })
        except Exception as e:
            logger.debug("baostock 写 %s 失败: %s", qlib_code_lower, e)
            fail_count += 1
        if (stock_idx + 1) % 200 == 0 or stock_idx + 1 == total_stocks:
            _up(pct=60 + (stock_idx + 1) / total_stocks * 30,
                status="running",
                message=f"baostock 写入 {stock_idx + 1}/{total_stocks} (成功{success_count})")

    return success_count, fail_count, pg_rows


def incremental_sync_eod_baostock(
    dates: list,
    codes: list,
    provider_uri: str,
    old_calendar: list,
    overwrite: bool = False,
    universe: str = "csi300",
) -> dict:
    """baostock 主源增量同步：对每个日期一次拉全市场，按股票分组写 bin。

    流程：
      1. 对每个 date 调 ``fetch_daily_all_a_stock_sync(date)`` 一次拉全市场
      2. code 列从 'sh.600000' 转 qlib 格式 'sh600000'（用 from_baostock_code）
      3. 数值列转 float，按股票池过滤
      4. 按 code 分组，每只股票调 ``_sync_stock_bin`` 写 bin（复用复权对齐逻辑）
      5. 提取 isST 字段供 ``_compute_tradable`` 判定 ST 5% 涨跌停

    Args:
        dates: 待同步日期列表（YYYY-MM-DD）
        codes: 股票池 qlib 代码列表（用于过滤全市场数据）
        provider_uri: qlib 数据目录
        old_calendar: 现有日历
        overwrite: 是否覆盖已有日期
        universe: 股票池名（仅用于日志/统计）

    Returns:
        dict: {ok, source, total_stocks, success, failed, skipped, dates, new_dates, ...}
    """
    try:
        from app.services.data.baostock_client import (
            BaostockQuotaError,
            fetch_daily_all_a_stock_sync,
            from_baostock_code,
        )
    except ImportError as e:
        # baostock_client 尚未就绪（Step1 并行开发中），返回失败由上层回退 akshare
        return {"ok": False, "error": f"baostock_client 未就绪: {e}"}

    if not dates:
        return {
            "ok": True, "source": "baostock", "universe": universe,
            "total_stocks": len(codes), "success": 0, "failed": 0, "skipped": 0,
            "dates": [], "new_dates": [],
            "calendar_before": len(old_calendar),
            "calendar_after": len(old_calendar),
        }

    # 写入字段：OHLCV + preclose/change/factor（后复权口径所需）+ tradable
    fields_to_write = list(FIELD_MAP.values()) + ["preclose", "change", "factor", "tradable"]

    per_stock_rows, fetched_dates, all_new_dates = _aggregate_baostock_days(
        dates, codes, old_calendar,
        fetch_daily_all_a_stock_sync, from_baostock_code, BaostockQuotaError,
    )

    # 按股票写 bin
    success_count, fail_count, pg_rows = _write_baostock_bins(
        per_stock_rows, provider_uri, old_calendar, fields_to_write, overwrite)

    # 更新日历（合并新日期）
    new_dates_sorted = sorted(all_new_dates)
    if new_dates_sorted:
        merged_cal = _merge_calendar(old_calendar, new_dates_sorted)
        # 先按新日历补齐所有 bin，再写 day.txt：避免"day.txt 已扩展、部分 bin
        # 仍是旧长度"的窗口——该窗口内回测/挖掘并发读会读到短 bin。先 pad 后写
        # 日历的窗口是"bin 长于 day.txt"，对齐前缀旧日期正确，读侧安全。
        _pad_bins_to_calendar(provider_uri, merged_cal)
        _write_calendar(provider_uri, merged_cal)
        logger.info("baostock 日历更新: %d -> %d (新增 %d 个交易日)",
                    len(old_calendar), len(merged_cal), len(new_dates_sorted))

    skipped = max(len(codes) - success_count - fail_count, 0)
    logger.info("baostock EOD 同步完成: 拉取日期%d, 成功%d, 失败%d, 跳过%d, 新增日期%d",
                len(fetched_dates), success_count, fail_count, skipped,
                len(new_dates_sorted))

    return {
        "ok": True,
        "source": "baostock",
        "universe": universe,
        "total_stocks": len(codes),
        "success": success_count,
        "failed": fail_count,
        "skipped": skipped,
        "dates": fetched_dates,
        "new_dates": new_dates_sorted,
        "pg_rows": pg_rows,  # 待 async 调用方落库 stock_daily（repair/校验以 PG 为权威）
        "calendar_before": len(old_calendar),
        "calendar_after": len(old_calendar) + len(new_dates_sorted),
    }


async def _refresh_status_after_eod(provider_uri: str, universe: str, codes: list) -> None:
    """EOD 同步后刷新 stock_data_status（latest_date 取实际落库最大交易日）。

    仅回填/repair 更新状态会导致 EOD 后 latest_date 不刷新（等下次回填才变）。
    这里复用 baostock_backfill._update_sync_status 统一口径。
    """
    try:
        from app.services.data.baostock_backfill import _update_sync_status
        calendar = _get_calendar(provider_uri)
        # code_range 仅用于 stock_count（len），用 codes 构造
        code_range = {c.lower(): [calendar[0], calendar[-1]] for c in codes} if calendar else {}
        await _update_sync_status(universe, provider_uri, calendar, code_range, sync_path="eod")
    except Exception as e:  # noqa: BLE001
        logger.warning("EOD 后刷新同步状态失败（可稍后回填修正）: %s", e)


async def incremental_sync_eod(
    universe: str = "csi300",
    days: int = 5,
    provider_uri: str = None,
    overwrite: bool = False,
    source: str = "baostock",
    include_intraday: bool = False,
) -> dict:
    """增量同步 EOD 数据。

    默认以 baostock 为主源（一次拉全市场，速度快）；baostock 失败时自动回退
    akshare（逐只爬）。也可通过 ``source='akshare'`` 显式走原逻辑。

    默认仅追加日历中不存在的新日期（overwrite=False），避免不同复权方式导致
    已有价格序列被覆盖。如需强制覆盖已有日期（例如修复缺失数据），设 overwrite=True。

    Args:
        universe: 股票池（csi300/csi500/all）
        days: 同步最近 N 天数据（1-30）
        provider_uri: qlib 数据目录，默认从 settings 读取
        overwrite: 是否覆盖日历中已有的日期数据（默认 False，仅追加新日期）
        source: 数据源，'baostock'（默认主源）或 'akshare'（逐只爬 fallback）

    Returns:
        dict: 同步结果，包含 ok/source/success/failed/new_dates 等
    """
    import asyncio

    from app.core.config import settings

    if provider_uri is None:
        provider_uri = settings.qlib_provider_path

    if not provider_uri or not os.path.exists(provider_uri):
        return {"ok": False, "error": f"qlib数据目录不存在: {provider_uri}"}

    logger.info("开始增量EOD同步: universe=%s, days=%d, source=%s, dir=%s",
                universe, days, source, provider_uri)

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

    # baostock 主源：一次拉全市场，按股票分组写 bin
    if source == "baostock":
        # 以「stock_daily 是否已有数据」判断已同步的交易日，而非 day.txt 日历：
        # 日历会被 padding 到当天，用它会把「日历已含但无数据」的当天判为已同步。
        covered = await _covered_trade_dates(start_date.date(), end_date.date())
        if covered is None:
            covered = set(old_calendar)  # DB 不可用 → 回退旧行为
        candidate_dates = _gen_candidate_dates(
            start_date, end_date, covered, overwrite,
        )
        # 盘中排除当日（15:00 前为 A 股交易时段，当日 bar 不完整）
        # include_intraday=True 时保留当日（供智能同步"同步当日"路径使用）
        today_str = datetime.now().strftime("%Y-%m-%d")
        if not include_intraday and datetime.now().hour < 15 and today_str in candidate_dates:
            candidate_dates = [d for d in candidate_dates if d != today_str]

        # 无新候选交易日（窗口内日期已全部在日历中）→ 数据已最新，直接返回。
        # 不能落 akshare：否则会对整个股票池逐只爬一遍（限速 3req/s）纯属浪费。
        if not candidate_dates:
            await _refresh_status_after_eod(provider_uri, universe, codes)
            return {
                "ok": True, "source": "baostock", "universe": universe,
                "success": 0, "failed": 0, "skipped": 0,
                "dates": [], "new_dates": [],
                "message": "无新交易日，数据已最新",
            }

        try:
            loop = asyncio.get_running_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    incremental_sync_eod_baostock,
                    candidate_dates, codes, provider_uri, old_calendar, overwrite, universe,
                ),
                timeout=600,
            )
            # ok=True 且实际取到数据(success>0)才采纳；否则（失败/异常/桩返回空）回退 akshare 兜底
            if result.get("ok") and result.get("success", 0) > 0:
                await _insert_pg_rows(result.get("pg_rows") or [], source="baostock")
                result.pop("pg_rows", None)  # 不入 eod_last_result.json（量大无意义）
                await _refresh_status_after_eod(provider_uri, universe, codes)
                return result
            logger.warning(
                "baostock 主源未取到数据(ok=%s, success=%d)，回退 akshare: %s",
                result.get("ok"), result.get("success", 0), result.get("error", ""),
            )
        except TimeoutError:
            logger.warning("baostock 主源超时，回退 akshare")
        except Exception as e:
            logger.warning("baostock 主源异常，回退 akshare: %s", e)
        # 落到 akshare fallback

    # akshare fallback：逐只爬
    result = await _incremental_sync_eod_akshare(
        codes, start_str, end_str, old_calendar, provider_uri,
        universe, days, overwrite, include_intraday,
    )
    await _refresh_status_after_eod(provider_uri, universe, codes)
    return result


async def _insert_pg_rows(rows: list, source: str = "") -> None:
    """EOD 增量路径把当日数据落库 stock_daily（幂等）。

    repair / validation 以 PG stock_daily 为日历权威：EOD 若只写 bin+day.txt
    而不落库，任何一次 repair 都会把 EOD 独有的日期从 day.txt 删除，随后按新
    日历从 PG 重建 bin 时把 EOD 数据截断丢失。落库失败只告警、不阻断 bin 写入。
    """
    if not rows:
        return
    import asyncio
    try:
        from app.services.data.baostock_backfill import _insert_stock_daily  # 延迟导入避免循环依赖
        await asyncio.wait_for(_insert_stock_daily(rows), timeout=300)
    except Exception as e:  # noqa: BLE001
        logger.warning("EOD %s 落库 stock_daily 失败（%d 行）: %s", source, len(rows), e)


async def _incremental_sync_eod_akshare(
    codes: list,
    start_str: str,
    end_str: str,
    old_calendar: list,
    provider_uri: str,
    universe: str,
    days: int,
    overwrite: bool = False,
    include_intraday: bool = False,
) -> dict:
    """akshare fallback 路径：逐只拉取日K数据并写 bin。

    保留原 incremental_sync_eod 的逐只爬逻辑，作为 baostock 主源失败时的兜底。
    优先东财源（含涨跌幅），失败回退新浪源（无涨跌幅，用 close 近似计算）。
    """
    import asyncio
    from functools import partial

    from app.core.ratelimit import get_akshare_bucket

    cal_set = set(old_calendar) if old_calendar else set()
    loop = asyncio.get_running_loop()
    # 令牌桶限速：替代固定 sleep 间隔，平均速率稳定且允许小幅突发
    # 在 executor 提交前 acquire，避免线程池里多个 fetch 同时打满 akshare
    rate_bucket = get_akshare_bucket()

    success_count = 0
    fail_count = 0
    skip_count = 0
    all_new_dates = set()
    pg_rows = []  # stock_daily 落库记录（仅新日期）
    from app.services.data.baostock_backfill import _f  # 延迟导入避免循环依赖

    for i, qlib_code in enumerate(codes):
        try:
            # 限速：每次调用 akshare 前取 1 个令牌（默认 3 req/s）
            await rate_bucket.acquire(timeout=60)
            fn = partial(_fetch_eod_akshare, qlib_code, start_str, end_str)
            df = await asyncio.wait_for(loop.run_in_executor(None, fn), timeout=30)

            if df is None or df.empty:
                skip_count += 1
                continue

            # 过滤盘中不完整数据：15:00 前为 A 股交易时段，当日 bar 不完整
            # include_intraday=True 时保留当日（供智能同步"同步当日"路径使用）
            today_str = datetime.now().strftime("%Y-%m-%d")
            if not include_intraday and datetime.now().hour < 15:
                df = df[df["date"] != today_str]
                if df.empty:
                    skip_count += 1
                    continue

            # 收集新日期
            for d in df["date"].tolist():
                if d not in cal_set:
                    all_new_dates.add(d)

            # stock_daily 记录（仅新日期；已落库的旧日期由回填补齐，不重复写）
            for r in df.to_dict("records"):
                d = str(r["date"])[:10]
                if d not in cal_set:
                    pg_rows.append({
                        "code": qlib_code.upper(),
                        "trade_date": d,
                        "open": _f(r.get("open")), "high": _f(r.get("high")),
                        "low": _f(r.get("low")), "close": _f(r.get("close")),
                        "volume": _f(r.get("volume")),
                        "pct_chg": _f(r.get("pct_change")),
                    })

            # 计算涨跌停 mask（akshare 路径无 isST，is_st=None 按板块阈值，向后兼容）
            if "pct_change" in df.columns:
                pct = df["pct_change"]
            else:
                # 新浪源无涨跌幅，用 close 变化率近似（分数转百分数以统一单位）
                pct = df["close"].pct_change().fillna(0.0) * 100.0
            df["tradable"] = _compute_tradable(
                df["close"], pct, code=qlib_code, is_st=None,
            )

            # 为该股票写入各字段的 bin 文件（含 tradable）
            feat_dir = os.path.join(provider_uri, "features", qlib_code.lower())
            _sync_stock_bin(
                feat_dir, df, old_calendar,
                list(FIELD_MAP.values()) + ["tradable"],
                overwrite,
            )
            success_count += 1

        except TimeoutError:
            logger.debug("拉取 %s 超时", qlib_code)
            fail_count += 1
        except Exception as e:
            logger.debug("拉取 %s 失败: %s", qlib_code, e)
            fail_count += 1

        # 进度日志 + 进度推送
        if (i + 1) % 100 == 0 or i + 1 == len(codes):
            from app.services.data.sync_progress import update_progress as _up
            logger.info("EOD同步进度: %d/%d (成功%d, 失败%d, 跳过%d)",
                        i + 1, len(codes), success_count, fail_count, skip_count)
            _up(pct=10 + (i + 1) / len(codes) * 80,
                status="running",
                message=f"akshare 同步 {i + 1}/{len(codes)} (成功{success_count},失败{fail_count})")

    # 更新日历（合并新日期）
    new_dates_sorted = sorted(all_new_dates)
    if new_dates_sorted:
        merged_cal = _merge_calendar(old_calendar, new_dates_sorted)
        # 先按新日历补齐所有 bin，再写 day.txt（避免"day.txt 已扩展而 bin 未对齐"的并发读窗口）
        _pad_bins_to_calendar(provider_uri, merged_cal)
        _write_calendar(provider_uri, merged_cal)
        logger.info("日历更新: %d -> %d (新增 %d 个交易日)",
                    len(old_calendar), len(merged_cal), len(new_dates_sorted))

    # EOD 新增交易日落库 stock_daily（否则 repair 以 PG 为权威会丢 EOD 数据）
    if pg_rows:
        await _insert_pg_rows(pg_rows, source="akshare")

    logger.info("EOD增量同步完成(akshare): 成功%d, 失败%d, 跳过%d, 新增日期%d",
                success_count, fail_count, skip_count, len(new_dates_sorted))

    return {
        # ok 必须反映实际写入：全部失败(success=0)时返回 False，
        # 避免上层 finish_progress(True) 把"什么都没拉到"标记成成功。
        "ok": success_count > 0,
        "source": "akshare",
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
                    global_calendar: list, fields, overwrite: bool = False,
                    old_calendar: list = None):
    """将单只股票数据同步到 bin 文件（统一日历契约）。

    写入策略（重构后）：
      - bin 文件 = 4 字节 start_index 头 + float32 数组，start_index 恒为 0
      - 数据数组始终与全局日历对齐：arr[i] 对应 global_calendar[i]
      - 分三种写入模式，消除"补 1 天/补 1 批就把整段历史读出再全量写回"的放大：
        * create    文件不存在：一次性写全长 NaN 数组并填本次日期
        * positions 文件长度已 == 目标日历长度：只定点覆盖本次日期的字节
        * append    新日期严格位于旧日历尾部之后（EOD 增量）：文件尾追加字节
        其余复杂场景（旧 bin 错位 / 日历头部前插 / 需复权比例对齐）走 legacy
        全量重写，语义与旧实现完全一致。
      - overwrite=True 用新数据覆盖所有匹配日期；False 仅写入日历中不存在的新日期。

    注意：merged_calendar 仅用于确定新日期的索引位置。全局日历合并
    由调用方在所有股票处理完成后统一执行。

    old_calendar: 旧 bin 实际对齐的日历（历史回填时旧 bin 对齐的是"上次的
        day.txt"，它位于 global_calendar 的**后缀**而非前缀；不传则沿用旧行为，
        按 global_calendar 前缀映射——当新日期比旧数据更早时会把旧数据散到
        错误位置，导致最近一段历史丢失）。
    """
    if not global_calendar:
        # 日历为空，无法定位索引（极端情况）
        return

    cal_set = set(global_calendar)

    # 分离已有日期和新日期
    df_dates = df["date"].tolist()
    new_dates_in_df = sorted([d for d in df_dates if d not in cal_set])

    # 合并后的日历（仅用于确定新日期的索引）
    merged_cal = _merge_calendar(global_calendar, new_dates_in_df)
    merged_idx = {d: i for i, d in enumerate(merged_cal)}
    n_merged = len(merged_cal)
    # 旧 bin 实际对齐的日历（决定旧数据在合并日历里的位置）
    ref_calendar = old_calendar if old_calendar is not None else global_calendar
    ref_n = len(ref_calendar)

    # 筛选需要写入的日期：overwrite=True 时全部写入，否则仅写入新日期
    if overwrite:
        write_pairs = list(zip(df_dates, range(len(df_dates)), strict=False))
    else:
        write_pairs = [(d, i) for i, d in enumerate(df_dates) if d not in cal_set]

    if not write_pairs and new_dates_in_df:
        # new_dates_in_df 非空但 write_pairs 为空（理论上不会发生）
        write_pairs = [(d, df_dates.index(d)) for d in new_dates_in_df]
    if not write_pairs:
        # 没有需要写入的日期：不需要触碰 bin（旧实现会全量重写一遍，纯浪费）
        return

    # 复权基准比例只在"非 overwrite 且新数据覆盖旧日历已有日期"时可能出现；
    # 一旦出现必须走 legacy 全量路径（需用旧数据末值求比例）。
    need_ratio = (not overwrite) and any(d in cal_set for d in df_dates)

    for field in fields:
        if field not in df.columns:
            continue
        bin_path = os.path.join(feat_dir, f"{field}.day.bin")
        new_values = df[field].values.astype(np.float32)

        old_start, old_len = _read_bin_meta(bin_path)
        exists = old_start is not None and old_len is not None and old_len > 0

        if not exists:
            # create：一次性写全长 NaN + 本次日期，后续批次走 positions/append
            arr = np.full(n_merged, np.nan, dtype=np.float32)
            for d, row_i in write_pairs:
                arr[merged_idx[d]] = new_values[row_i]
            _write_bin(bin_path, arr, 0)
            continue

        if old_start == 0 and not need_ratio:
            if old_len == n_merged:
                # positions：长度已与目标日历一致，只定点覆盖本次日期（不重写历史）
                positions = []
                vals = []
                for d, row_i in write_pairs:
                    pos = merged_idx.get(d)
                    if pos is not None and 0 <= pos < n_merged:
                        positions.append(pos)
                        vals.append(new_values[row_i])
                if positions:
                    _write_bin_positions(bin_path, positions, vals)
                continue
            if (not overwrite and old_len == ref_n
                    and n_merged > old_len
                    and all(merged_idx.get(d, -1) >= old_len for d, _ in write_pairs)):
                # append：增量日期严格在旧日历尾部之后，文件尾追加对应 float32
                tail = np.full(n_merged - old_len, np.nan, dtype=np.float32)
                for d, row_i in write_pairs:
                    pos = merged_idx.get(d)
                    if pos is not None and old_len <= pos < n_merged:
                        tail[pos - old_len] = new_values[row_i]
                _write_bin_append(bin_path, tail)
                continue

        # ---- legacy 全量重写（对齐校验/丢弃重建/复权比例/头部前插等场景）----
        old_values, old_start = _read_bin(bin_path)
        old_aligned = (
            old_values is not None
            and len(old_values) > 0
            and old_start >= 0
            and old_start + len(old_values) <= n_merged
        )
        if old_aligned:
            # 按日期映射重建数组（用旧 bin 真实对齐的日历，而非 global_calendar 前缀）
            mapping = _build_index_mapping(
                ref_calendar, old_start, len(old_values), merged_cal,
            )
            arr = np.full(n_merged, np.nan, dtype=np.float32)
            # 散布旧数据（保留已有值）
            valid = mapping >= 0
            if valid.any():
                arr[mapping[valid]] = old_values[valid]

            # ===== 复权基准对齐 =====
            # 历史数据与增量数据价格口径不一致时，通过旧数据最后一个有效值与
            # 新数据同日值计算复权比例，将新数据乘以比例后再写入，避免价格跳变。
            if field in PRICE_FIELDS and not overwrite:
                old_valid_indices = np.where(~np.isnan(old_values))[0]
                if len(old_valid_indices) > 0:
                    old_last_idx = old_valid_indices[-1]
                    old_last_value = float(old_values[old_last_idx])
                    cal_pos = old_start + old_last_idx
                    if 0 <= cal_pos < len(ref_calendar):
                        old_last_date = ref_calendar[cal_pos]
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
        else:
            # 旧 bin 与当前日历不对齐：丢弃重建（并记录，便于排查）
            arr = np.full(n_merged, np.nan, dtype=np.float32)
            if old_values is not None and len(old_values) > 0:
                logger.warning(
                    "bin 与日历不对齐，丢弃旧数据重建 %s "
                    "(old_start=%d, old_len=%d, cal_len=%d)",
                    bin_path, old_start,
                    len(old_values) if old_values is not None else 0,
                    n_merged,
                )

        # 写入新数据（仅指定日期）
        for d, row_i in write_pairs:
            arr[merged_idx[d]] = new_values[row_i]
        _write_bin(bin_path, arr, 0)
