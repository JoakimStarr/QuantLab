"""ETF 数据同步：baostock 全市场 ETF 日K → qlib bin + etf_daily 窄表。

与 A 股回填同构：按交易日一次拉全市场（``query_daily_history_k_ETF``），
写 qlib bin（OHLCV+amount+change+tradable+factor），落 etf_daily 窄表，
并把已同步的全部 ETF 写入 ``instruments/etf_all.txt``（拉多少存多少，不过滤）。

设计要点：
- ETF 复用 A 股 day.txt 为主日历（ETF 与 A 股同交易日），bin 对齐同一日历，
  日历新增日由 ``_pad_bins_to_calendar`` 统一补齐。
- ETF bin 只写 OHLCV 子集：无 pe_ttm/is_st/turn 等股票字段，factor 恒 1.0
  （价格按前复权存储）；tradable = 非停牌（tradestatus==1），无涨跌停判定。
- etf_daily 窄表不混入 stock_daily，避免 fieldset/日历一致性检查污染。
- 全量池：baostock 返回多少 ETF 就存多少，不做精选/成交额筛选。
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ETF bin 字段：OHLCV + amount + 衍生字段（无股票专属字段）
ETF_BIN_FIELDS = ["open", "high", "low", "close", "volume", "amount",
                  "change", "tradable", "factor"]

# 每批处理的交易日数：控制拉取/写 bin 的内存与单次落库量
_CHUNK_DAYS = int(os.environ.get("QUANTLAB_ETF_CHUNK_DAYS", "20"))


def _f(v):
    """float 转换，NaN/None -> None。"""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    try:
        f = float(v)
        return None if np.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _etf_out_df(df: pd.DataFrame) -> pd.DataFrame:
    """把某 ETF 的多日 baostock 行转成 qlib bin 写入帧。"""
    df = df.sort_values("date").reset_index(drop=True)
    out = pd.DataFrame({
        "date": df["date"].astype(str),
        "open": df["open"].astype(float),
        "high": df["high"].astype(float),
        "low": df["low"].astype(float),
        "close": df["close"].astype(float),
        "volume": df["volume"].astype(float),
        "amount": df["amount"].astype(float) if "amount" in df.columns else np.nan,
    })
    # change = 涨跌幅(小数)
    out["change"] = (df["pctChg"].astype(float) / 100.0).fillna(0.0)
    # ETF 无涨跌停/ST：tradable = 非停牌（tradestatus==1）；缺失/停牌按不可交易
    ts = df["tradestatus"] if "tradestatus" in df.columns else pd.Series("1", index=df.index)
    out["tradable"] = pd.to_numeric(ts, errors="coerce").fillna(0.0).clip(0.0, 1.0)
    # 价格按前复权存储，qlib 依赖 factor 常量 1.0 判定已复权
    out["factor"] = 1.0
    return out


def sync_etf_to_qlib(provider_uri: str, dates: list, old_calendar: list,
                     overwrite: bool = False) -> dict:
    """按日拉全市场 ETF 写 bin，返回结果（含待落库 pg_rows）。

    运行在线程池（同步阻塞 baostock）；调用方负责把 pg_rows 落 etf_daily。
    """
    from app.services.data.baostock_client import (
        BaostockQuotaError, fetch_etf_daily_sync, from_baostock_code,
    )
    from app.services.data.eod_incremental import _sync_stock_bin

    if not dates:
        return {"ok": True, "success": 0, "failed": 0, "dates": [],
                "new_dates": [], "etf_codes": [], "pg_rows": []}

    cal_set = set(old_calendar) if old_calendar else set()
    per_code = {}
    all_new_dates = set()
    fetched_dates = []

    for date_idx, d in enumerate(dates):
        try:
            df_all = fetch_etf_daily_sync(d)
        except BaostockQuotaError as e:
            logger.error("ETF 同步中止: %s", e)
            break
        except Exception as e:  # noqa: BLE001
            logger.warning("ETF 拉取 %s 失败: %s", d, e)
            continue
        if df_all is None or df_all.empty:
            continue
        fetched_dates.append(d)
        if d not in cal_set:
            all_new_dates.add(d)

        df_all = df_all.copy()
        df_all["qlib_code"] = df_all["code"].apply(from_baostock_code)
        df_all["qlib_code_lower"] = df_all["qlib_code"].str.lower()
        df_all["date"] = pd.to_datetime(df_all["date"]).dt.strftime("%Y-%m-%d")
        for c in ["open", "high", "low", "close", "volume", "amount", "pctChg", "tradestatus"]:
            if c in df_all.columns:
                df_all[c] = pd.to_numeric(df_all[c], errors="coerce")
        for code_lower, grp in df_all.groupby("qlib_code_lower"):
            per_code.setdefault(code_lower, []).append(grp)

    success = 0
    fail = 0
    pg_rows = []
    for code_lower, grps in per_code.items():
        try:
            df = pd.concat(grps, ignore_index=True)
            out = _etf_out_df(df)
            feat_dir = os.path.join(provider_uri, "features", code_lower)
            _sync_stock_bin(feat_dir, out, old_calendar, ETF_BIN_FIELDS, overwrite)
            success += 1
            # etf_daily 窄表记录（ON CONFLICT DO NOTHING 幂等）
            for _, r in df.iterrows():
                pg_rows.append({
                    "code": code_lower.upper(),
                    "trade_date": str(r["date"])[:10],
                    "open": _f(r.get("open")), "high": _f(r.get("high")),
                    "low": _f(r.get("low")), "close": _f(r.get("close")),
                    "volume": _f(r.get("volume")), "amount": _f(r.get("amount")),
                    "pct_chg": _f(r.get("pctChg")),
                })
        except Exception as e:  # noqa: BLE001
            logger.debug("ETF 写 %s 失败: %s", code_lower, e)
            fail += 1

    return {
        "ok": True, "success": success, "failed": fail,
        "dates": fetched_dates, "new_dates": sorted(all_new_dates),
        "etf_codes": sorted(per_code.keys()), "pg_rows": pg_rows,
    }


async def _load_etf_existing_dates() -> set:
    """已落库 etf_daily 的交易日集合（YYYY-MM-DD）。"""
    from sqlalchemy import select
    from app.core.database import async_session
    from app.models.baostock import EtfDaily

    async with async_session() as session:
        result = await session.execute(select(EtfDaily.trade_date).distinct())
        return {r[0].strftime("%Y-%m-%d") for r in result}


async def _insert_etf_daily(rows: list, upsert: bool = False) -> None:
    """批量落库 etf_daily（分批防 asyncpg 参数上限）。

    upsert=True 时覆盖已有行（腾讯 qfq 对齐用：修正 baostock 口径）；
    默认 ON CONFLICT DO NOTHING（幂等）。
    """
    if not rows:
        return
    from datetime import date as _date
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.core.database import async_session
    from app.models.baostock import EtfDaily

    BATCH = 1000
    async with async_session() as session:
        for i in range(0, len(rows), BATCH):
            chunk = rows[i:i + BATCH]
            for r in chunk:
                r["trade_date"] = _date.fromisoformat(r["trade_date"])
            stmt = pg_insert(EtfDaily.__table__).values(chunk)
            if upsert:
                stmt = stmt.on_conflict_do_update(
                    index_elements=["code", "trade_date"],
                    set_={k: getattr(stmt.excluded, k) for k in
                          ("open", "high", "low", "close", "volume", "amount", "pct_chg")},
                )
            else:
                stmt = stmt.on_conflict_do_nothing(index_elements=["code", "trade_date"])
            await session.execute(stmt)
        await session.commit()


async def _register_synced_etfs(codes: list) -> int:
    """注册已同步 ETF 到 stock_index（type='etf'），名称尽量从 stock_basic 取。"""
    from sqlalchemy import select
    from app.core.database import async_session
    from app.models.baostock import StockBasic
    from app.services.data.index_registry import register_etf

    name_map = {}
    try:
        async with async_session() as session:
            rows = await session.execute(
                select(StockBasic.code, StockBasic.name).where(StockBasic.type == "5")
            )
            name_map = {str(r[0]).lower(): r[1] for r in rows.all()}
    except Exception as e:  # noqa: BLE001
        logger.warning("加载 ETF 名称失败: %s", e)

    added = 0
    for c in sorted(set(codes)):
        if await register_etf(c, name_map.get(c.lower())):
            added += 1
    return added


# ============ 腾讯源（qfq 对齐回填） ============

_TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def fetch_etf_history_tencent(qlib_code: str, start: str, end: str) -> pd.DataFrame | None:
    """腾讯 fqkline/get 单次拉取（qfq 前复权）。

    窗口 ≤800 交易日时一次拿全（腾讯单次上限 800 条），无需分页。
    列序转换：[date, open, close, high, low, volume(手)] →
    date/open/high/low/close/volume(股) + amount(估算) + pctChg(自算) + tradestatus。

    Args:
        qlib_code: qlib 小写代码（如 sh510300）
        start/end: YYYY-MM-DD
    Returns:
        DataFrame（兼容 _etf_out_df 输入），失败返回 None
    """
    import requests
    try:
        r = requests.get(
            _TENCENT_KLINE_URL,
            params={"param": f"{qlib_code},day,{start},{end},800,qfq"},
            timeout=15,
        )
        node = r.json().get("data", {}).get(qlib_code)
        if not node:
            return None
        kline = node.get("qfqday") or node.get("day") or []
        if not kline:
            return None
        rows = []
        for k in kline:
            d = str(k[0])
            if d < start or d > end:
                continue
            rows.append({
                "date": d,
                "open": float(k[1]), "high": float(k[3]), "low": float(k[4]),
                "close": float(k[2]),
                # 腾讯成交量单位为手（1手=100份），转为股与 baostock 口径一致
                "volume": float(k[5]) * 100.0,
            })
        df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
        if df.empty:
            return None
        # 腾讯 kline 不含成交额：用成交量 × 均价估算
        df["amount"] = df["volume"] * (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0
        # 涨跌幅自算（qfq 序列无除权跳变，pct_change 即真实涨跌幅）
        df["pctChg"] = (df["close"].pct_change() * 100.0).fillna(0.0)
        df["tradestatus"] = 1
        return df
    except Exception as e:  # noqa: BLE001
        logger.warning("腾讯拉取 %s 失败: %s", qlib_code, e)
        return None


async def _load_etf_min_date() -> str | None:
    """etf_daily 最早交易日（YYYY-MM-DD），无数据返回 None。"""
    from sqlalchemy import func, select
    from app.core.database import async_session
    from app.models.baostock import EtfDaily

    async with async_session() as session:
        v = (await session.execute(select(func.min(EtfDaily.trade_date)))).scalar()
        return v.strftime("%Y-%m-%d") if v else None


async def _load_etf_codes_from_db() -> list:
    """etf_daily 全部去重代码（大写）。"""
    from sqlalchemy import select
    from app.core.database import async_session
    from app.models.baostock import EtfDaily

    async with async_session() as session:
        result = await session.execute(select(EtfDaily.code).distinct())
        return sorted({r[0] for r in result.all()})


async def sync_etf_tencent_aligned(provider_uri: str = None, days: int = None,
                                   overwrite: bool = True) -> dict:
    """腾讯 qfq 对齐回填：只拉"现有 etf_daily 时间范围"，不拉全历史。

    每只 ETF 一次请求（窗口 ≤800 交易日），qfq 复权价修正 baostock 口径，
    upsert 覆盖 etf_daily，重建全量池。日历以 A 股 day.txt 为准，不扩展。
    """
    from app.core.config import settings
    from app.services.data.eod_incremental import _get_calendar, _sync_stock_bin

    provider_uri = provider_uri or settings.qlib_provider_path
    calendar = _get_calendar(provider_uri)
    if len(calendar) < 2:
        return {"ok": False, "error": "qlib 日历为空，请先同步 A 股数据"}

    # 对齐窗口：现有 etf_daily 最早日期 → 主日历末日；无数据时回退 days 窗口
    existing_min = await _load_etf_min_date()
    end = calendar[-1]
    if existing_min:
        start = existing_min
    else:
        start = (datetime.now() - timedelta(days=days or 730)).strftime("%Y-%m-%d")
    cal_set = set(calendar)

    codes = await _load_etf_codes_from_db()
    if not codes:
        return {"ok": False, "error": "etf_daily 无数据，请先用 baostock 同步一次建立时间范围"}

    total = len(codes)
    success = fail = 0
    pg_rows = []
    for idx, code in enumerate(codes):
        df = fetch_etf_history_tencent(code.lower(), start, end)
        if df is None or df.empty:
            fail += 1
            continue
        # 对齐主日历：只保留 day.txt 内的日期
        df = df[df["date"].isin(cal_set)]
        if df.empty:
            fail += 1
            continue
        try:
            out = _etf_out_df(df)
            feat_dir = os.path.join(provider_uri, "features", code.lower())
            _sync_stock_bin(feat_dir, out, calendar, ETF_BIN_FIELDS, overwrite=overwrite)
            for _, r in df.iterrows():
                pg_rows.append({
                    "code": code, "trade_date": str(r["date"])[:10],
                    "open": _f(r.get("open")), "high": _f(r.get("high")),
                    "low": _f(r.get("low")), "close": _f(r.get("close")),
                    "volume": _f(r.get("volume")), "amount": _f(r.get("amount")),
                    "pct_chg": _f(r.get("pctChg")),
                })
            success += 1
        except Exception as e:  # noqa: BLE001
            logger.debug("腾讯写 %s 失败: %s", code, e)
            fail += 1
        if (idx + 1) % 200 == 0 or idx + 1 == total:
            logger.info("腾讯 ETF 对齐进度: %d/%d (成功%d 失败%d)", idx + 1, total, success, fail)
            await _insert_etf_daily(pg_rows, upsert=True)
            pg_rows = []
    if pg_rows:
        await _insert_etf_daily(pg_rows, upsert=True)

    pool = await rebuild_etf_pool(provider_uri)
    registered = await _register_synced_etfs(codes)
    return {
        "ok": True, "source": "tencent", "success": success, "failed": fail,
        "window": [start, end], "etf_count": total, "pool_count": len(pool),
        "registered": registered,
    }


async def rebuild_etf_pool(provider_uri: str = None) -> list:
    """把已同步的全部 ETF 写入 instruments/etf_all.txt（拉多少存多少，不过滤）。

    用户明确要求：不做精选/成交额筛选，baostock 返回多少就存多少。
    该池是"全量 ETF 池"，前端在标的池下拉里以 etf_all 出现。

    Returns:
        list: 全部 ETF 的 qlib 小写代码（写入 instruments/etf_all.txt）。
    """
    from sqlalchemy import select
    from app.core.config import settings
    from app.core.database import async_session
    from app.models.baostock import EtfDaily
    from app.services.data.eod_incremental import _get_calendar

    provider_uri = provider_uri or settings.qlib_provider_path
    calendar = _get_calendar(provider_uri)
    if len(calendar) < 2:
        logger.warning("日历为空，无法重建 ETF 池")
        return []

    async with async_session() as session:
        result = await session.execute(select(EtfDaily.code).distinct())
        codes = sorted({r[0] for r in result.all()})

    path = os.path.join(provider_uri, "instruments", "etf_all.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for code in codes:
            f.write(f"{code.lower()}\t{calendar[0]}\t{calendar[-1]}\n")
    logger.info("重建 ETF 全量池: %d 只（写入 instruments/etf_all.txt）", len(codes))
    return [c.lower() for c in codes]


async def sync_etf_task(provider_uri: str = None, days: int = 730,
                        overwrite: bool = False) -> dict:
    """ETF 一键同步：拉历史 → 写 bin → 落 etf_daily → 重建全量池 → 注册。

    Args:
        days: 回看窗口（自然日），默认 730（约 2 年交易日）。
        overwrite: 是否覆盖已有日期（默认仅补新日期）。

    Returns:
        dict: 统计信息（success/failed/pool_count/registered 等）。
    """
    from app.core.config import settings
    from app.services.data.eod_incremental import _get_calendar

    provider_uri = provider_uri or settings.qlib_provider_path
    old_calendar = _get_calendar(provider_uri)
    if not old_calendar:
        return {"ok": False, "error": "qlib 日历为空，请先同步 A 股数据"}

    end_ts = datetime.now()
    start_ts = end_ts - timedelta(days=days)
    start_str = start_ts.strftime("%Y-%m-%d")
    end_str = end_ts.strftime("%Y-%m-%d")

    # 候选日期 = A 股主日历窗口内的交易日（ETF 与 A 股同交易日）
    candidate = [d for d in old_calendar if start_str <= d <= end_str]
    if not overwrite:
        existing = await _load_etf_existing_dates()
        candidate = [d for d in candidate if d not in existing]
    if not candidate:
        return {"ok": True, "success": 0, "message": "ETF 数据已最新，无新日期需同步"}

    loop = asyncio.get_running_loop()
    total_success = total_failed = 0
    fetched_dates = []
    etf_codes = set()
    for i in range(0, len(candidate), _CHUNK_DAYS):
        chunk = candidate[i:i + _CHUNK_DAYS]
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None, sync_etf_to_qlib, provider_uri, chunk, old_calendar, overwrite,
            ),
            timeout=3600,
        )
        if not result.get("ok"):
            return result
        await _insert_etf_daily(result.get("pg_rows") or [])
        total_success += result.get("success", 0)
        total_failed += result.get("failed", 0)
        fetched_dates.extend(result.get("dates") or [])
        etf_codes.update(result.get("etf_codes") or [])
        logger.info("ETF 同步进度: %d/%d 天（成功%d 失败%d）",
                    min(i + _CHUNK_DAYS, len(candidate)), len(candidate),
                    total_success, total_failed)

    curated = await rebuild_etf_pool(provider_uri)
    registered = await _register_synced_etfs(list(etf_codes))

    return {
        "ok": True, "success": total_success, "failed": total_failed,
        "dates": fetched_dates, "etf_count": len(etf_codes),
        "pool_count": len(curated), "registered": registered,
    }
