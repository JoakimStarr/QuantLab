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
from datetime import datetime
from pathlib import Path
from functools import partial
from typing import Callable, Optional

import pandas as pd
from app.core.config import settings
from app.core.errors import DataFetchError
from app.services.data.code_utils import to_qlib_code
from app.services.data.data_clean import format_date_series

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


# A 股列表进程级缓存（1 小时），避免每次搜索/选股池打 akshare 网络请求。
# 收拢 data_ext 与 get_universe 各自维护的重复缓存。
_stock_list_cache: Optional[list[dict]] = None
_stock_list_updated_at: Optional[datetime] = None
_STOCK_LIST_TTL_SECONDS = 3600


async def get_stock_list(force_refresh: bool = False) -> list[dict]:
    """获取 A 股列表 [{code, name, qlib_code}]（1 小时进程级缓存）。"""
    global _stock_list_cache, _stock_list_updated_at
    now = datetime.now()
    if not force_refresh and _stock_list_cache is not None and _stock_list_updated_at is not None:
        if (now - _stock_list_updated_at).total_seconds() < _STOCK_LIST_TTL_SECONDS:
            return _stock_list_cache
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
    _stock_list_cache = items
    _stock_list_updated_at = now
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
    df["date"] = format_date_series(df["date"])
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
    """使用 qlib 官方 DumpDataAll 进行 bin 转储。

    从 CSV 目录批量转储为 qlib bin 格式：
    - calendars/day.txt
    - instruments/all.txt
    - features/<code>/<field>.day.bin
    """
    from app.services.quant.qlib_dump import DumpAll

    dump = DumpAll(
        data_path=csv_dir,
        qlib_dir=qlib_dir,
        include_fields=",".join(include_fields),
        date_field_name="date",
        file_suffix=".csv",
        max_workers=1,
    )
    dump.dump()

    # 同时写入配置的 universe 文件（factor_eval 按 universe 读取）
    universe = settings.quant.get("universe", "all")
    if universe != "all":
        inst_dir = Path(qlib_dir) / "instruments"
        src = inst_dir / "all.txt"
        dst = inst_dir / f"{universe}.txt"
        if src.exists():
            import shutil
            shutil.copy2(str(src), str(dst))

    logger.info(
        "qlib bin 转储完成(使用 qlib DumpDataAll): csv_dir=%s, qlib_dir=%s, fields=%s",
        csv_dir, qlib_dir, include_fields,
    )


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
