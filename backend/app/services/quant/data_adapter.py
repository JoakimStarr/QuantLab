"""AKShare -> qlib bin 数据适配层。

职责：
1. 通过 AKShare 拉取 A 股列表与日线 OHLCV
2. 转为 qlib 期望的 CSV 格式（按品种一个文件）
3. 调用 qlib DumpAll 转储为 bin（含 calendars/instruments）

qlib 代码格式：sh600000 / sz000001 / bj830799
AKShare 返回 6 位代码，需按前缀映射交易所：
  6xxxxx -> sh（沪市主板）
  688xxx -> sh（科创板）
  0xxxxx -> sz（深市主板）
  3xxxxx -> sz（创业板）
  8xxxxx/9xxxxx/4xxxxx -> bj（北交所）
"""
import logging
import tempfile
import asyncio
from pathlib import Path
from functools import partial
from typing import Callable, Optional

import pandas as pd
import numpy as np

from app.core.config import settings
from app.core.errors import DataFetchError
from app.services.data.code_utils import to_qlib_code

logger = logging.getLogger(__name__)


async def _run_async(func, *args, timeout: int = 30, **kwargs):
    """在线程池中运行同步 AKShare 函数。"""
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, partial(func, *args, **kwargs)),
            timeout=timeout,
        )
        return result
    except asyncio.TimeoutError:
        raise DataFetchError(f"AKShare 请求超时 (timeout={timeout}s)")


def _get_stock_list_sync() -> pd.DataFrame:
    """同步获取 A 股代码与名称列表。"""
    import akshare as ak
    return ak.stock_info_a_code_name()


async def get_stock_list() -> list[dict]:
    """获取 A 股列表 [{code, name, qlib_code}]。"""
    df = await _run_async(_get_stock_list_sync, timeout=60)
    if df is None or df.empty:
        raise DataFetchError("获取 A 股列表为空")
    col_code = "code" if "code" in df.columns else df.columns[0]
    col_name = "name" if "name" in df.columns else df.columns[1]
    items = []
    for _, row in df.iterrows():
        code = str(row[col_code]).strip().zfill(6)
        items.append({
            "code": code,
            "name": str(row[col_name]).strip(),
            "qlib_code": to_qlib_code(code),
        })
    return items


def _get_index_constituents_sync(index: str) -> list[str]:
    """获取指数成分股代码列表。index: csi300/csi500/csi1000。"""
    import akshare as ak
    mapping = {
        "csi300": ak.index_stock_cons_csindex,
        "csi500": ak.index_stock_cons_csindex,
        "csi1000": ak.index_stock_cons_csindex,
    }
    symbol_map = {
        "csi300": "000300",
        "csi500": "000905",
        "csi1000": "000852",
    }
    fn = mapping.get(index, ak.index_stock_cons_csindex)
    symbol = symbol_map.get(index, "000300")
    df = fn(symbol=symbol)
    if df is None or df.empty:
        return []
    # 成分股代码列名可能是“成分券代码”或“ Constituents Stock Code”
    code_col = next((c for c in df.columns if "代码" in str(c) or "code" in str(c).lower()), df.columns[0])
    return [str(v).strip().zfill(6) for v in df[code_col].tolist()]


async def get_universe() -> list[str]:
    """根据 config.quant.universe 返回 A 股代码列表（6 位）。"""
    universe = settings.quant.get("universe", "csi300")
    if universe == "all":
        items = await get_stock_list()
        return [it["code"] for it in items]
    codes = await _run_async(_get_index_constituents_sync, universe, timeout=60)
    return codes


def _fetch_daily_sync(code: str, start: str, end: str, adjust: str) -> pd.DataFrame:
    """同步拉取单只股票前复权日线。"""
    import akshare as ak
    return ak.stock_zh_a_hist(
        symbol=code, period="daily",
        start_date=start.replace("-", ""), end_date=end.replace("-", ""),
        adjust=adjust or "",
    )


async def fetch_stock_daily(code: str, start: str, end: str) -> pd.DataFrame:
    """拉取单只股票日线 OHLCV，标准化列名与 qlib 字段。"""
    adjust = settings.quant.get("adjust", "qfq")
    df = await _run_async(_fetch_daily_sync, code, start, end, adjust, timeout=30)
    if df is None or df.empty:
        return pd.DataFrame()
    # AKShare 返回列：日期 开盘 收盘 最高 最低 成交量 成交额 ...
    col_map = {
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
        "成交额": "amount", "换手率": "turnover",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    if "date" not in df.columns:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    keep = ["date", "open", "close", "high", "low", "volume"]
    keep = [c for c in keep if c in df.columns]
    return df[keep]


def _write_qlib_csv(qlib_code: str, df: pd.DataFrame, out_dir: Path) -> bool:
    """写入单个品种 CSV（qlib DumpAll 期望的格式）。"""
    if df is None or df.empty or "date" not in df.columns:
        return False
    target = out_dir / f"{qlib_code}.csv"
    df.to_csv(target, index=False)
    return True


def _dump_to_qlib_bin(csv_dir: str, qlib_dir: str, include_fields: list[str]) -> None:
    """将 CSV 目录转为 qlib bin 格式（calendars + instruments + features/<code>/<field>.day.bin）。

    qlib bin 格式（FileFeatureStorage）：
    - calendars/day.txt：每行一个日期字符串
    - instruments/all.txt：tab 分隔 code\\tstart\\tend
    - features/<code>/<field>.day.bin：[start_index:float32] + [data:float32...]（小端）
    """
    qlib_path = Path(qlib_dir)
    cal_dir = qlib_path / "calendars"
    inst_dir = qlib_path / "instruments"
    feat_dir = qlib_path / "features"
    for d in (cal_dir, inst_dir, feat_dir):
        d.mkdir(parents=True, exist_ok=True)

    # 1. 读取所有 CSV，构建全局日历
    csv_files = sorted(Path(csv_dir).glob("*.csv"))
    if not csv_files:
        raise ValueError("CSV 目录为空")
    per_inst = {}  # qlib_code -> DataFrame(index=date)
    all_dates = set()
    for f in csv_files:
        code = f.stem  # e.g. sh600000
        df = pd.read_csv(f)
        if "date" not in df.columns:
            continue
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        df = df.drop_duplicates(subset="date").set_index("date").sort_index()
        per_inst[code] = df
        all_dates.update(df.index.tolist())

    calendar = sorted(all_dates)
    # 2. 写日历
    with (cal_dir / "day.txt").open("w", encoding="utf-8") as fp:
        fp.write("\n".join(calendar) + "\n")
    cal_index = {d: i for i, d in enumerate(calendar)}

    # 3. 写 instruments 与 features
    inst_rows = []
    for code, df in per_inst.items():
        start_d = df.index[0]
        end_d = df.index[-1]
        inst_rows.append(f"{code}\t{start_d}\t{end_d}")
        code_feat_dir = feat_dir / code
        code_feat_dir.mkdir(parents=True, exist_ok=True)
        start_idx = cal_index[start_d]
        for field in include_fields:
            if field not in df.columns:
                continue
            # 对齐全局日历（从 start_idx 开始）
            aligned = df[field].reindex(calendar[start_idx:]).astype(np.float32)
            arr = np.concatenate([[np.float32(start_idx)], aligned.values.astype("<f4")])
            arr.tofile(code_feat_dir / f"{field}.day.bin")

    with (inst_dir / "all.txt").open("w", encoding="utf-8") as fp:
        fp.write("\n".join(inst_rows) + "\n")
    # 同时写入配置的 universe 文件（factor_eval 按 universe 读取）
    universe = settings.quant.get("universe", "all")
    if universe != "all":
        with (inst_dir / f"{universe}.txt").open("w", encoding="utf-8") as fp:
            fp.write("\n".join(inst_rows) + "\n")
    logger.info("qlib bin 转储完成: %d 品种, %d 字段, %d 交易日", len(per_inst), len(include_fields), len(calendar))


async def sync_to_qlib(
    start_date: str,
    end_date: Optional[str] = None,
    codes: Optional[list[str]] = None,
    progress_cb: Optional[Callable[[dict], None]] = None,
) -> dict:
    """全量/增量同步 A 股日线到 qlib bin 目录。

    Args:
        start_date: 起始日期 YYYY-MM-DD
        end_date: 截止日期，默认今天
        codes: 指定代码列表，None 则用 config.universe
        progress_cb: 进度回调 {total, done, failed, current, qlib_code}
    Returns:
        统计 dict
    """
    from datetime import datetime
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")
    if codes is None:
        codes = await get_universe()

    total = len(codes)
    done = 0
    failed = 0
    interval = settings.quant.get("fetch_interval_seconds", 1.2)
    max_workers = settings.quant.get("fetch_max_workers", 3)
    qlib_dir = settings.qlib_provider_path

    logger.info("开始同步 %d 只股票到 qlib (%s -> %s)", total, start_date, end_date)

    # 串行+限频拉取（AKShare 限频严格，并发易被封）
    sem = asyncio.Semaphore(max_workers)

    async def fetch_one(code: str) -> tuple[str, pd.DataFrame]:
        async with sem:
            df = await fetch_stock_daily(code, start_date, end_date)
            await asyncio.sleep(interval)
            return code, df

    with tempfile.TemporaryDirectory(prefix="qlib_csv_") as tmpdir:
        tmp_path = Path(tmpdir)
        tasks = [fetch_one(c) for c in codes]
        for coro in asyncio.as_completed(tasks):
            code = "unknown"
            try:
                code, df = await coro
                qlib_code = to_qlib_code(code)
                ok = await asyncio.get_running_loop().run_in_executor(
                    None, _write_qlib_csv, qlib_code, df, tmp_path
                )
                if ok:
                    done += 1
                else:
                    failed += 1
            except Exception as e:
                logger.warning("拉取失败 %s: %s", code, e)
                failed += 1
            if progress_cb:
                progress_cb({
                    "total": total, "done": done, "failed": failed,
                    "current": code, "qlib_code": to_qlib_code(code),
                })

        # 转储 bin
        include_fields = ["open", "close", "high", "low", "volume"]
        await asyncio.get_running_loop().run_in_executor(
            None, _dump_to_qlib_bin, str(tmp_path), qlib_dir, include_fields
        )

    summary = {
        "total": total, "done": done, "failed": failed,
        "start_date": start_date, "end_date": end_date,
        "qlib_dir": qlib_dir,
    }
    logger.info("同步完成: %s", summary)
    return summary
