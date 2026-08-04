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
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
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
                df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
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
                         old_calendar: list, overwrite: bool) -> list:
    """生成候选同步日期（YYYY-MM-DD）。

    遍历 [start_date, end_date] 区间内的工作日；overwrite=False 时跳过日历已有日期，
    以减少对 baostock 的调用次数。非交易日（周末/节假日）由 baostock 返回空数据自然跳过。
    """
    cal_set = set(old_calendar) if old_calendar else set()
    dates = []
    cur = start_date
    while cur <= end_date:
        if cur.weekday() < 5:  # 仅工作日（周一至周五）
            d = cur.strftime("%Y-%m-%d")
            if overwrite or d not in cal_set:
                dates.append(d)
        cur += timedelta(days=1)
    return dates


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

    cal_set = set(old_calendar) if old_calendar else set()
    codes_set = set(c.lower() for c in codes)
    # 写入字段与 akshare 路径保持一致：open/high/low/close/volume + tradable
    fields_to_write = list(FIELD_MAP.values()) + ["tradable"]

    # 按股票聚合各日数据：qlib_code_lower -> list[DataFrame]
    per_stock_rows = {}
    all_new_dates = set()
    fetched_dates = []

    from app.services.data.sync_progress import update_progress as _up
    total_dates = len(dates) if dates else 1
    for date_idx, date in enumerate(dates):
        try:
            df_all = fetch_daily_all_a_stock_sync(date)
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
        df_all["date"] = pd.to_datetime(df_all["date"]).dt.strftime("%Y-%m-%d")

        # 数值列转 float（baostock 可能返回字符串/对象类型）
        num_cols = ["open", "high", "low", "close", "volume", "amount",
                    "pctChg", "isST"]
        for c in num_cols:
            if c in df_all.columns:
                df_all[c] = pd.to_numeric(df_all[c], errors="coerce")

        for qlib_code_lower, grp in df_all.groupby("qlib_code_lower"):
            per_stock_rows.setdefault(qlib_code_lower, []).append(grp)

    # 按股票写 bin
    success_count = 0
    fail_count = 0
    total_stocks = len(per_stock_rows) if per_stock_rows else 1
    for stock_idx, (qlib_code_lower, grps) in enumerate(per_stock_rows.items()):
        try:
            df = pd.concat(grps, ignore_index=True)
            df = df.sort_values("date").reset_index(drop=True)
            qlib_code = qlib_code_lower.upper()

            # 构造写入 DataFrame（字段与 akshare 路径一致）
            out = pd.DataFrame({
                "date": df["date"].astype(str),
                "open": df["open"].astype(float),
                "high": df["high"].astype(float),
                "low": df["low"].astype(float),
                "close": df["close"].astype(float),
                "volume": df["volume"].astype(float),
                "pct_change": df["pctChg"].astype(float),
            })
            # isST: baostock '1'=ST, '0'=非ST，转 bool 供 ST 5% 涨跌停判定
            if "isST" in df.columns:
                is_st = df["isST"].astype(str) == "1"
            else:
                is_st = None
            out["tradable"] = _compute_tradable(
                out["close"], out["pct_change"], code=qlib_code, is_st=is_st,
            )

            feat_dir = os.path.join(provider_uri, "features", qlib_code_lower)
            # 复用现有复权对齐逻辑（baostock 不复权价与旧 bin 通过 ratio 对齐）
            _sync_stock_bin(feat_dir, out, old_calendar, fields_to_write, overwrite)
            success_count += 1
        except Exception as e:
            logger.debug("baostock 写 %s 失败: %s", qlib_code_lower, e)
            fail_count += 1
        if (stock_idx + 1) % 200 == 0 or stock_idx + 1 == total_stocks:
            _up(pct=60 + (stock_idx + 1) / total_stocks * 30,
                status="running",
                message=f"baostock 写入 {stock_idx + 1}/{total_stocks} (成功{success_count})")

    # 更新日历（合并新日期）
    new_dates_sorted = sorted(all_new_dates)
    if new_dates_sorted:
        merged_cal = _merge_calendar(old_calendar, new_dates_sorted)
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
        "calendar_before": len(old_calendar),
        "calendar_after": len(old_calendar) + len(new_dates_sorted),
    }


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
        candidate_dates = _gen_candidate_dates(
            start_date, end_date, old_calendar, overwrite,
        )
        # 盘中排除当日（15:00 前为 A 股交易时段，当日 bar 不完整）
        # include_intraday=True 时保留当日（供智能同步"同步当日"路径使用）
        today_str = datetime.now().strftime("%Y-%m-%d")
        if not include_intraday and datetime.now().hour < 15 and today_str in candidate_dates:
            candidate_dates = [d for d in candidate_dates if d != today_str]

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
            # ok=True 且实际取到数据(success>0)才采纳；否则（失败/异常/桩返回空）回退 akshare
            if result.get("ok") and result.get("success", 0) > 0:
                return result
            logger.warning(
                "baostock 主源未取到数据(ok=%s, success=%d)，回退 akshare: %s",
                result.get("ok"), result.get("success", 0), result.get("error", ""),
            )
        except asyncio.TimeoutError:
            logger.warning("baostock 主源超时，回退 akshare")
        except Exception as e:
            logger.warning("baostock 主源异常，回退 akshare: %s", e)
        # 落到 akshare fallback

    # akshare fallback：逐只爬
    return await _incremental_sync_eod_akshare(
        codes, start_str, end_str, old_calendar, provider_uri,
        universe, days, overwrite, include_intraday,
    )


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

        except asyncio.TimeoutError:
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
        _write_calendar(provider_uri, merged_cal)
        logger.info("日历更新: %d -> %d (新增 %d 个交易日)",
                    len(old_calendar), len(merged_cal), len(new_dates_sorted))

    logger.info("EOD增量同步完成(akshare): 成功%d, 失败%d, 跳过%d, 新增日期%d",
                success_count, fail_count, skip_count, len(new_dates_sorted))

    return {
        "ok": True,
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
                    global_calendar: list, fields, overwrite: bool = False):
    """将单只股票数据同步到 bin 文件（统一日历契约）。

    设计：
      - bin 文件 = 4 字节 start_index 头 + float32 数组，start_index 恒为 0
      - 数据数组始终与全局日历对齐：arr[i] 对应 global_calendar[i]
      - 旧 bin 仅在数据范围落在当前全局日历内时（old_start + len <= 日历长度）
        按日期映射保留；否则视为日历变更导致的错位数据，丢弃重建，
        避免"首尾重复/数据错位"类损坏累积。
      - overwrite=True 用新数据覆盖所有匹配日期；False 仅写入日历中不存在的新日期。

    注意：merged_calendar 仅用于确定新日期的索引位置。全局日历合并
    由调用方在所有股票处理完成后统一执行。
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

        # ---- 旧 bin 对齐校验 ----
        # 旧 bin 数据范围（[old_start, old_start + len)）必须落在当前合并日历内，
        # 否则说明旧 bin 是对齐到另一份日历写入的（日历被覆盖/缩短过），
        # 无法安全映射 → 丢弃旧数据，仅用新数据重建。
        old_aligned = (
            old_values is not None
            and len(old_values) > 0
            and old_start >= 0
            and old_start + len(old_values) <= len(merged_cal)
        )
        if old_aligned:
            # 按日期映射重建数组
            mapping = _build_index_mapping(
                global_calendar, old_start, len(old_values), merged_cal,
            )
            arr = np.full(len(merged_cal), np.nan, dtype=np.float32)
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
                    if 0 <= cal_pos < len(global_calendar):
                        old_last_date = global_calendar[cal_pos]
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
            arr = np.full(len(merged_cal), np.nan, dtype=np.float32)
            if old_values is not None and len(old_values) > 0:
                logger.warning(
                    "bin 与日历不对齐，丢弃旧数据重建 %s "
                    "(old_start=%d, old_len=%d, cal_len=%d)",
                    bin_path, old_start,
                    len(old_values) if old_values is not None else 0,
                    len(merged_cal),
                )

        # 写入新数据（仅指定日期）
        for d, row_i in write_pairs:
            if d in merged_idx:
                arr[merged_idx[d]] = new_values[row_i]
        _write_bin(bin_path, arr, 0)
