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
import pandas as pd
from datetime import datetime

from app.core.config import settings
from app.services.data.eod_incremental import (
    _get_calendar,
    _sync_stock_bin,
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

# 指数中文名（注册 stock_index 主表用，便于校验时区分指数与股票）
INDEX_NAMES: dict[str, str] = {
    "sh000001": "上证指数",
    "sh000300": "沪深300",
    "sh000016": "上证50",
    "sh000905": "中证500",
    "sh000852": "中证1000",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000688": "科创50",
}

# qlib 指数字段（收敛到 data_fields.py）
from app.services.data.data_fields import INDEX_FIELDS


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
    from app.services.data.baostock_client import (
        to_baostock_code,
        _ensure_login,
        _consume_request_slot,
    )

    bs_code = to_baostock_code(qlib_code)  # sh000001 -> sh.000001
    _ensure_login()
    _consume_request_slot()
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


def _fetch_index_via_akshare(qlib_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """通过 akshare 拉取单个指数日K（baostock 缺失指数如科创50 的兜底）。

    双源：优先东财 index_zh_a_hist（列 日期/开盘/...），失败时回退新浪
    stock_zh_index_daily（列 date/open/high/low/close/volume，按 qlib 全代码）。
    新浪源对科创50等新指数更稳定，东财近期常出现 RemoteDisconnected 断连。
    归一化为 date,open,high,low,close,volume，与 baostock 分支一致。
    """
    import akshare as ak

    empty = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    # 源1：东财
    symbol = qlib_code[2:]  # sh000688 -> 000688
    try:
        df = ak.index_zh_a_hist(
            symbol=symbol, period="daily",
            start_date=str(start_date).replace("-", ""),
            end_date=str(end_date).replace("-", ""),
        )
        if df is not None and not df.empty:
            return pd.DataFrame({
                "date": df["日期"].astype(str),
                "open": pd.to_numeric(df["开盘"], errors="coerce"),
                "high": pd.to_numeric(df["最高"], errors="coerce"),
                "low": pd.to_numeric(df["最低"], errors="coerce"),
                "close": pd.to_numeric(df["收盘"], errors="coerce"),
                "volume": pd.to_numeric(df["成交量"], errors="coerce"),
            })
    except Exception as e:
        logger.warning("akshare 东财指数 %s 拉取失败，尝试新浪: %s", qlib_code, str(e)[:120])

    # 源2：新浪（date 为 datetime.date，astype(str) 即 YYYY-MM-DD）
    try:
        df = ak.stock_zh_index_daily(symbol=qlib_code)
        if df is None or df.empty:
            return empty
        df = df[["date", "open", "high", "low", "close", "volume"]].copy()
        df["date"] = df["date"].astype(str)
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df
    except Exception as e:
        logger.warning("akshare 新浪指数 %s 拉取失败: %s", qlib_code, str(e)[:120])
        return empty


def sync_indices_to_qlib(provider_uri: str, indices: list = None, days: int = 365) -> dict:
    """同步指数到 qlib bin（baostock 为主，akshare 兜底缺失指数）。

    指数清单由 config.quant.sync_indices 配置，默认含 8 大指数。
    指数同步不扩展日历（以现有 day.txt 为准对齐写入）。

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

    index_list = _get_index_list(indices)
    success = 0
    failed = 0
    indices_synced = []
    sources_used: set[str] = set()

    # 前端进度：逐指数更新（worker 已 init_progress("indices")）
    from app.services.data.sync_progress import update_progress

    total_ind = len(index_list)

    # 拉取日期范围：从日历起始到今天
    start_date = calendar[0]
    end_date = datetime.now().strftime("%Y-%m-%d")

    from app.services.data.baostock_client import (
        BaostockQuotaError, _ensure_login, ensure_logout,
    )

    # 连续失败熔断：baostock 连接衰减（10002007）时逐只重试只是浪费——
    # 连续 5 次异常 → 重建会话一次；重建后再连续 3 次 → 熔断 baostock，
    # 剩余指数直接走 akshare 兜底（不同网络路径，大概率仍可用）。
    consecutive_fail = 0
    relogin_done = False
    baostock_broken = False
    abort_reason = None

    for idx, qlib_code in enumerate(index_list):
        update_progress(
            pct=5 + int(90 * idx / max(total_ind, 1)), status="running",
            message=f"同步指数 {idx + 1}/{total_ind}（{qlib_code}）...",
        )
        try:
            df = None
            source_used = "akshare"
            if not baostock_broken:
                try:
                    df = _fetch_index_via_baostock(qlib_code, start_date, end_date)
                    source_used = "baostock"
                    consecutive_fail = 0
                except BaostockQuotaError:
                    raise
                except Exception as e_bs:  # noqa: BLE001
                    consecutive_fail += 1
                    df = None
                    logger.warning("指数 %s baostock 拉取异常（连续 %d 次）: %s",
                                   qlib_code, consecutive_fail, e_bs)
                    if consecutive_fail >= 5 and not relogin_done:
                        logger.warning("baostock 指数拉取连续失败 %d 次，尝试重建会话", consecutive_fail)
                        try:
                            ensure_logout()
                            _ensure_login()
                            relogin_done = True
                            consecutive_fail = 0
                            logger.info("baostock 会话已重建，继续指数拉取")
                        except Exception as e2:  # noqa: BLE001
                            logger.error("baostock 会话重建失败: %s", e2)
                            # 重建失败也标记已尝试：否则 relogin_done 恒为 False，
                            # 熔断分支永不可达，baostock 全挂时逐只空试浪费
                            relogin_done = True
                    elif relogin_done and consecutive_fail >= 3:
                        baostock_broken = True
                        abort_reason = f"baostock 连续 {consecutive_fail} 次失败熔断，剩余指数改用 akshare"
                        logger.error("指数同步熔断 baostock: %s", abort_reason)
            if df is None or df.empty:
                # baostock 无数据/异常/熔断 → akshare 兜底（不同数据源，独立可用）
                logger.info("指数 %s baostock 无数据，尝试 akshare 兜底", qlib_code)
                df = _fetch_index_via_akshare(qlib_code, start_date, end_date)
                source_used = "akshare"
            if df is None or df.empty:
                logger.warning("指数 %s 无数据（baostock+akshare 均无）", qlib_code)
                failed += 1
                continue

            # 只保留日历中的日期（不扩展日历）
            df = df[df["date"].isin(cal_set)]
            if df.empty:
                logger.warning("指数 %s 过滤后无数据", qlib_code)
                failed += 1
                continue

            # 写入 bin 文件（复用 _sync_stock_bin 统一日历契约与原子写盘）
            feat_dir = os.path.join(provider_uri, "features", qlib_code.lower())
            out = pd.DataFrame({"date": df["date"].astype(str)})
            for field in INDEX_FIELDS:
                if field in df.columns:
                    out[field] = pd.to_numeric(df[field], errors="coerce")
            _sync_stock_bin(feat_dir, out, calendar, INDEX_FIELDS, overwrite=True)

            logger.info("指数 %s 同步完成(%s): %d 条数据", qlib_code, source_used, len(df))
            success += 1
            indices_synced.append(qlib_code)
            sources_used.add(source_used)

        except BaostockQuotaError as e:
            # 当日请求配额耗尽，中止整个指数同步，避免逐只无谓重试
            logger.error("指数同步中止: %s", e)
            abort_reason = str(e)
            break
        except Exception as e:
            logger.error("指数 %s 同步失败: %s", qlib_code, e)
            failed += 1

    source = "+".join(sorted(sources_used)) if sources_used else "none"
    logger.info("指数同步完成: 成功%d, 失败%d, 共%d（baostock熔断=%s）",
                success, failed, len(index_list), baostock_broken)
    return {
        "ok": True,
        "success": success,
        "failed": failed,
        "indices": indices_synced,
        "total": len(index_list),
        "source": source,
        "aborted": bool(abort_reason and not baostock_broken),
        "abort_reason": abort_reason,
    }
