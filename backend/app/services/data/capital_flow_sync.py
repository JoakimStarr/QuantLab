"""资金/情绪数据采集器：北向资金 + 融资融券 + 龙虎榜 + 大单流向。

转为 QLib bin 字段，使因子表达式可直接引用：
  $north_net      - 北向资金个股净买入额（元）
  $margin_balance - 融资融券余额（元）
  $dragon_net     - 龙虎榜净买入额（元，仅上榜日有值）
  $big_order_net  - 大单净流入额（元）

数据源：akshare（免费）
存储：QLib bin（日频，与OHLCV同目录），缺失日填NaN
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta
from functools import partial

import numpy as np
import pandas as pd

from app.core.config import settings
from app.core.errors import DataFetchError
from app.services.data.eod_incremental import (
    _read_bin, _write_bin, _get_calendar,
    _merge_calendar, _build_index_mapping,
)

logger = logging.getLogger(__name__)

# 资金字段名（QLib bin字段名，因子表达式用 $north_net 引用）
CAPITAL_FIELDS = ["north_net", "margin_balance", "dragon_net", "big_order_net"]


async def _run_async(func, *args, timeout: int = 30, **kwargs):
    """在线程池中运行同步 akshare 函数。"""
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, partial(func, *args, **kwargs)),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        raise DataFetchError(f"akshare 请求超时 (timeout={timeout}s)")


def _qlib_to_ak(qlib_code: str) -> str:
    """qlib代码 SH600000 -> 6位akshare代码 600000。"""
    c = qlib_code.upper()
    return c[2:] if c.startswith(("SH", "SZ", "BJ")) else c


# ============ 北向资金 ============

async def fetch_north_flow(code: str, start: str, end: str) -> pd.DataFrame:
    """拉取单只股票北向资金净买入额。

    akshare接口：stock_hsgt_individual_em(symbol=ak_code)
    返回字段含：日期、股票代码、持股数量、持股市值、持股数量占发行股百分比等
    净买入额需用相邻日持股市值差分计算，或用 stock_hsgt_individual_detail 接口

    Returns:
        DataFrame: 列含 date, north_net（净买入额，元）
    """
    import akshare as ak
    ak_code = _qlib_to_ak(code)
    try:
        # stock_hsgt_individual_em 返回历史持股明细
        df = await _run_async(ak.stock_hsgt_individual_em, symbol=ak_code, timeout=30)
        if df is None or df.empty:
            return pd.DataFrame()
        # 列名标准化（akshare版本差异，做best-effort）
        # 常见列：日期, 股票代码, 股票简称, 持股数量, 持股市值, 持股数量占发行股百分比
        rename = {"日期": "date", "持股市值": "hold_value"}
        df = df.rename(columns=rename)
        if "date" not in df.columns:
            return pd.DataFrame()
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        df = df[(df["date"] >= start) & (df["date"] <= end)].sort_values("date")
        # 净买入额 = 当日持股市值 - 前日持股市值
        if "hold_value" in df.columns:
            df["north_net"] = df["hold_value"].diff().fillna(0.0)
        else:
            df["north_net"] = 0.0
        return df[["date", "north_net"]]
    except Exception as e:
        logger.debug("北向资金 %s 失败: %s", code, e)
        return pd.DataFrame()


# ============ 融资融券 ============

async def fetch_margin(code: str, start: str, end: str) -> pd.DataFrame:
    """拉取单只股票融资融券余额。

    akshare接口：
    - 沪市：stock_margin_detail_sse(date=YYYYMMDD)  # 按日查全市场
    - 深市：stock_margin_detail_szse(date=YYYYMMDD)
    这两个接口是按日查全市场，效率低。改用 stock_margin_underlying_info_szse 或
    融资融券标的明细接口。

    实际可用接口：stock_margin_detail_sse 返回当日沪市全部融资融券明细。
    为避免逐日查询，用 stock_account_info_em 或周期查询。

    简化方案：用 ak.stock_margin_detail_szse(start_date, end_date) 批量拉取深市，
    沪市用 stock_margin_detail_sse。

    Returns:
        DataFrame: 列含 date, margin_balance（融资融券余额，元）
    """
    import akshare as ak
    ak_code = _qlib_to_ak(code)
    try:
        # 根据交易所选择接口
        c = code.upper()
        if c.startswith("SH"):
            # 沪市融资融券明细（按日，需循环日期，效率低）
            # TODO: 沪市融资融券明细接口 stock_margin_detail_sse 只支持单日查询，
            #       需要循环日期批量拉取后过滤代码。当前简化为返回空，后续优化。
            return pd.DataFrame()
        else:
            # 深市：stock_margin_detail_szse 可按区间拉取
            # TODO: 接口签名/字段名需运行时验证，akshare 版本可能不同
            df = await _run_async(
                ak.stock_margin_detail_szse,
                start_date=start.replace("-", ""),
                end_date=end.replace("-", ""),
                timeout=60,
            )
            if df is None or df.empty:
                return pd.DataFrame()
            rename = {"日期": "date", "融资融券余额": "margin_balance"}
            df = df.rename(columns=rename)
            if "date" not in df.columns:
                return pd.DataFrame()
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            # 按代码过滤
            code_col = "证券代码" if "证券代码" in df.columns else None
            if code_col:
                df = df[df[code_col].astype(str).str.zfill(6) == ak_code]
            return df[["date", "margin_balance"]] if "margin_balance" in df.columns else pd.DataFrame()
    except Exception as e:
        logger.debug("融资融券 %s 失败: %s", code, e)
        return pd.DataFrame()


# ============ 龙虎榜 ============

async def fetch_dragon_tiger(code: str, start: str, end: str) -> pd.DataFrame:
    """拉取单只股票龙虎榜净买入额。

    akshare接口：stock_lhb_detail_em(start_date, end_date)
    返回全市场龙虎榜明细，需按代码过滤。

    Returns:
        DataFrame: 列含 date, dragon_net（龙虎榜净买入额，元，仅上榜日有值）
    """
    import akshare as ak
    ak_code = _qlib_to_ak(code)
    try:
        # TODO: 字段名 "龙虎榜净买额" 需运行时验证，akshare 版本可能不同
        df = await _run_async(
            ak.stock_lhb_detail_em,
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            timeout=60,
        )
        if df is None or df.empty:
            return pd.DataFrame()
        rename = {"代码": "code", "日期": "date", "龙虎榜净买额": "dragon_net"}
        df = df.rename(columns=rename)
        if "date" not in df.columns:
            return pd.DataFrame()
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        df = df[(df["date"] >= start) & (df["date"] <= end)]
        # 按代码过滤
        if "code" in df.columns:
            df = df[df["code"].astype(str).str.zfill(6) == ak_code]
        if "dragon_net" not in df.columns:
            return pd.DataFrame()
        return df[["date", "dragon_net"]]
    except Exception as e:
        logger.debug("龙虎榜 %s 失败: %s", code, e)
        return pd.DataFrame()


# ============ 大单流向 ============

async def fetch_big_order(code: str, start: str, end: str) -> pd.DataFrame:
    """拉取单只股票大单净流入额。

    akshare接口：stock_individual_fund_flow(stock=ak_code, market="sh"/"sz")
    返回字段含：日期、收盘价、涨跌幅、主力净流入-净额、主力净流入-净占比、
    超大单净流入-净额、大单净流入-净额、中单净流入-净额、小单净流入-净额

    Returns:
        DataFrame: 列含 date, big_order_net（大单净流入额，元）
    """
    import akshare as ak
    ak_code = _qlib_to_ak(code)
    c = code.upper()
    market = "sh" if c.startswith("SH") else "sz"
    try:
        # TODO: 北交所 BJ 的市场代码未验证，当前按 sh/sz 二分
        df = await _run_async(
            ak.stock_individual_fund_flow,
            stock=ak_code, market=market, timeout=30,
        )
        if df is None or df.empty:
            return pd.DataFrame()
        # TODO: 字段名 "大单净流入-净额" 需运行时验证
        rename = {"日期": "date", "大单净流入-净额": "big_order_net"}
        df = df.rename(columns=rename)
        if "date" not in df.columns:
            return pd.DataFrame()
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        df = df[(df["date"] >= start) & (df["date"] <= end)]
        if "big_order_net" not in df.columns:
            return pd.DataFrame()
        return df[["date", "big_order_net"]]
    except Exception as e:
        logger.debug("大单流向 %s 失败: %s", code, e)
        return pd.DataFrame()


# ============ bin写入 ============

def _sync_capital_bin(
    feat_dir: str,
    df: pd.DataFrame,
    field: str,
    old_calendar: list,
    overwrite: bool = False,
):
    """将单只股票的单个资金字段同步到 bin 文件。

    复用 eod_incremental 的 _read_bin/_write_bin 逻辑，但资金字段不走复权对齐。

    Args:
        feat_dir: features/{code_lower} 目录
        df: 含 date 和 {field} 列的 DataFrame
        field: 字段名 north_net/margin_balance/dragon_net/big_order_net
        old_calendar: 现有QLib日历
        overwrite: 是否覆盖已有日期
    """
    if field not in df.columns or df.empty:
        return

    cal_set = set(old_calendar) if old_calendar else set()
    df_dates = df["date"].tolist()
    new_dates_in_df = sorted([d for d in df_dates if d not in cal_set])
    merged_cal = _merge_calendar(old_calendar, new_dates_in_df)
    merged_idx = {d: i for i, d in enumerate(merged_cal)}

    if overwrite:
        write_pairs = list(zip(df_dates, range(len(df_dates))))
    else:
        write_pairs = [(d, i) for i, d in enumerate(df_dates) if d not in cal_set]

    if not write_pairs:
        return

    bin_path = os.path.join(feat_dir, f"{field}.day.bin")
    old_values, old_start = _read_bin(bin_path)
    new_values = df[field].values.astype(np.float32)

    if old_values is None or len(old_values) == 0:
        arr = np.full(len(merged_cal), np.nan, dtype=np.float32)
        for d, row_i in write_pairs:
            if d in merged_idx:
                arr[merged_idx[d]] = new_values[row_i]
        _write_bin(bin_path, arr, 0)
    else:
        mapping = _build_index_mapping(old_calendar, old_start, len(old_values), merged_cal)
        arr = np.full(len(merged_cal), np.nan, dtype=np.float32)
        valid = mapping >= 0
        if valid.any():
            arr[mapping[valid]] = old_values[valid]
        for d, row_i in write_pairs:
            if d in merged_idx:
                arr[merged_idx[d]] = new_values[row_i]
        _write_bin(bin_path, arr, 0)


# ============ 主入口 ============

async def sync_capital_flow(
    codes: list,
    start: str = None,
    end: str = None,
    fields: list = None,
    overwrite: bool = False,
) -> dict:
    """同步资金/情绪数据到 QLib bin。

    Args:
        codes: qlib代码列表 ["SH600000", "SZ000001"]
        start: 起始日 YYYY-MM-DD，默认近60天
        end: 结束日 YYYY-MM-DD，默认今天
        fields: 要同步的字段列表，默认全部 ["north_net","margin_balance","dragon_net","big_order_net"]
        overwrite: 是否覆盖已有日期数据

    Returns:
        dict: ok/total/success/failed/field_stats
    """
    if end is None:
        end = datetime.now().strftime("%Y-%m-%d")
    if start is None:
        start = (datetime.now() - timedelta(days=75)).strftime("%Y-%m-%d")
    if fields is None:
        fields = CAPITAL_FIELDS

    provider_uri = settings.qlib_provider_path
    if not provider_uri or not os.path.exists(provider_uri):
        return {"ok": False, "error": f"qlib数据目录不存在: {provider_uri}"}

    old_calendar = _get_calendar(provider_uri)
    success = 0
    failed = 0
    field_stats = {f: 0 for f in fields}

    for i, code in enumerate(codes):
        try:
            feat_dir = os.path.join(provider_uri, "features", code.lower())
            os.makedirs(feat_dir, exist_ok=True)

            # 按字段拉取并写入
            for field in fields:
                if field == "north_net":
                    df = await fetch_north_flow(code, start, end)
                elif field == "margin_balance":
                    df = await fetch_margin(code, start, end)
                elif field == "dragon_net":
                    df = await fetch_dragon_tiger(code, start, end)
                elif field == "big_order_net":
                    df = await fetch_big_order(code, start, end)
                else:
                    continue

                if df is not None and not df.empty:
                    _sync_capital_bin(feat_dir, df, field, old_calendar, overwrite)
                    field_stats[field] += 1

            success += 1
        except Exception as e:
            logger.debug("资金同步 %s 失败: %s", code, e)
            failed += 1

        if (i + 1) % 100 == 0:
            logger.info("资金同步进度: %d/%d (成功%d, 失败%d)", i + 1, len(codes), success, failed)

    logger.info("资金/情绪同步完成: total=%d success=%d failed=%d 字段统计=%s",
                len(codes), success, failed, field_stats)
    return {
        "ok": True,
        "total": len(codes),
        "success": success,
        "failed": failed,
        "field_stats": field_stats,
    }
