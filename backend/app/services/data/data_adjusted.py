"""后复权（hfq）价格变换：本地重建 qlib bin 的复权价与真实复权因子。

背景（量化正确性）
------------------
qlib 的 ``backtest/exchange.py`` 用 ``$close`` 估值、用 ``$factor`` 做整手取整，
其中 ``factor = 复权价 / 真实价``。此前 bin 存的是 **不复权原始价 + factor=1.0**，
qlib 会把原始价当复权价使用，除权日产生伪跳空，回测/因子全错。

baostock 全市场接口 ``query_daily_history_k_AStock`` 不支持 adjustflag（只能拿到
原始价），但可从 bin 里已有的字段本地重建：

    官方 baostock「涨跌幅复权法」：
        增量后复权因子  e_t = 前一日收盘价 / 当日前收(preclose)   （正常日 e=1，除权日 e>1）
        累计后复权因子  A_t = cumprod(e_t)
        后复权价        hfq_t = 原始价_t × A_t

    ``preclose`` 由数据源直接给出（已按当日除权调整），因此 e_t 只需相邻两根 bar 即可
    推出；A_t 是累计量，所以一段错误的 preclose 会「污染」其后所有 bar，写入前必须校验。

口径
----
- bin ：hfq 复权价（OHLC + preclose 同乘 A_t），factor = A_t（真实复权因子）。
- PG  ：stock_daily 存 **原始价**（baostock 原样），与 bin 通过 ``raw = bin/factor`` 互推。
- 展示：前端行情用 qfq（前复权）口径，与本模块无关。
- 派生字段 ``change``（=pctChg/100，已是除权调整后的日收益）与 ``volume``/``amount``
  不受复权影响，保持原样；标签使用 ``$change``，不受本变换影响。

跨厂商差异
----------
baostock 的「涨跌幅复权法」以 preclose 递推累计因子；不同厂商（akshare 东财/新浪、
通联等）对分红送转的复权基准与精度处理略有出入，绝对值可能有微小差异，
但日收益一致（本仓库实测重建 hfq 与官方 hfq 比值恒定、std≈7.5e-09）。
"""
from __future__ import annotations

import logging
import os
import struct

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 需要乘复权因子的字段：OHLC + preclose。
# preclose 必须与 close 同乘 A_t：hfq_preclose_t = 原始preclose_t × A_t = hfq_close_{t-1}，
# 否则 bin 内 close/preclose-1 ≠ change，校验会失败。
ADJUSTED_COLS: tuple[str, ...] = ("open", "high", "low", "close", "preclose")
PRICE_COLS: tuple[str, ...] = ("open", "high", "low", "close")

# 复权一致性容差：|close/preclose - 1 - change|（change 为小数日收益）
CHANGE_TOL = 1e-4
# e_t 接近 1 时吸附为 1，抑制浮点噪声在累计 A 上的漂移
_E_SNAP_TOL = 1e-9

QLIB_BIN_HEADER_SIZE = 4
QLIB_BIN_DTYPE = "<f4"


def compute_hfq_factor(raw_close, preclose) -> pd.Series:
    """按 baostock 官方涨跌幅复权法计算累计后复权因子 A_t。

    ``e_t = raw_close.shift(1) / preclose``；正常日/首日/无效值吸附为 1；
    ``A_t = cumprod(e_t)``。

    Args:
        raw_close: 原始（不复权）收盘价序列，按时间升序
        preclose: 当日前收序列（已含当日除权调整），与 raw_close 对齐

    Returns:
        pd.Series[float]: 与输入同索引的累计后复权因子 A_t（首根 bar 恒为 1）
    """
    close = pd.to_numeric(pd.Series(raw_close).reset_index(drop=True), errors="coerce")
    pre = pd.to_numeric(pd.Series(preclose).reset_index(drop=True), errors="coerce")
    prev = close.shift(1)
    with np.errstate(divide="ignore", invalid="ignore"):
        e = prev / pre
    # 正常日/首日/除零/NaN → 1；仅保留有限正数
    e = e.where(np.isfinite(e) & (e > 0), 1.0)
    e = e.where(~((e - 1.0).abs() < _E_SNAP_TOL), 1.0)
    a = e.cumprod()
    a = a.where(np.isfinite(a) & (a > 0), 1.0)
    a.index = pd.Series(raw_close).index
    return a


def validate_change_consistency(raw_close, preclose, change, tol: float = CHANGE_TOL) -> list:
    """校验 |raw_close/preclose - 1 - change| < tol，返回越界位置（列表）。

    仅校验 close/preclose/change 均为有限值的行；preclose<=0 视为无效跳过。
    返回值是位置索引（0-based，按输入顺序），供调用方定位异常 bar。
    """
    close = pd.to_numeric(pd.Series(raw_close).reset_index(drop=True), errors="coerce")
    pre = pd.to_numeric(pd.Series(preclose).reset_index(drop=True), errors="coerce")
    chg = pd.to_numeric(pd.Series(change).reset_index(drop=True), errors="coerce")
    valid = (
        np.isfinite(close) & np.isfinite(pre) & (pre != 0)
        & np.isfinite(chg)
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        implied = close / pre - 1.0
    diff = (implied - chg).abs()
    bad = valid & (diff >= tol)
    return [int(i) for i in np.nonzero(bad.to_numpy())[0]]


def recover_raw_prices(df: pd.DataFrame, *, price_cols=ADJUSTED_COLS,
                       factor_col: str = "factor") -> pd.DataFrame:
    """从已复权的 DataFrame 还原原始价：``raw = price / factor``。

    幂等辅助：factor 缺失/为 0/为 1 的行不还原（原始价即复权价）。
    """
    out = df.copy()
    if factor_col not in out.columns:
        return out
    f = pd.to_numeric(out[factor_col], errors="coerce")
    mask = f.notna() & (f != 0) & (f != 1.0)
    if mask.any():
        for c in price_cols:
            if c in out.columns:
                out.loc[mask, c] = pd.to_numeric(out.loc[mask, c], errors="coerce") / f[mask]
    return out


def apply_hfq_transform(df: pd.DataFrame, *, price_cols=ADJUSTED_COLS,
                        base_factor: float | None = None,
                        prev_raw_close: float | None = None) -> pd.DataFrame:
    """对（按时间升序的）行情 DataFrame 应用后复权变换。

    - 全量模式（``base_factor is None``）：A_t 从该段首根 bar 起累计（首根 A=1）。
      传入的 df 必须是该股票 **完整** 原始价序列，结果才是全局一致的 hfq。
    - 增量模式（``base_factor`` 给定）：df 为新增（更晚）bar，A_t = base_factor × 段内累计；
      可选 ``prev_raw_close``（上一根已存 bar 的原始收盘）用于补上首根 bar 的边界除权因子
      ``e_0 = prev_raw_close / preclose_0``。
    - 幂等：若 df 已带 ``factor`` 列，先按 ``raw = price/factor`` 还原后重算，
      重复执行不会二次复权。
    - 只缩放 ``open/high/low/close/preclose``，设置 ``factor = A_t``；
      volume/amount/change/tradable 原样保留。
    """
    out = df.copy()
    # 幂等：先还原原始价（若已复权）
    out = recover_raw_prices(out, price_cols=price_cols, factor_col="factor")
    if "close" not in out.columns or "preclose" not in out.columns:
        # 缺 preclose 无法计算因子，退化为原样（factor=1）避免误伤
        out["factor"] = base_factor if base_factor is not None else 1.0
        return out

    a = compute_hfq_factor(out["close"], out["preclose"])
    if base_factor is not None:
        if (prev_raw_close is not None and len(out) > 0):
            pre0 = pd.to_numeric(pd.Series([out["preclose"].iloc[0]]), errors="coerce").iloc[0]
            if pd.notna(pre0) and pre0 != 0 and pd.notna(prev_raw_close):
                e0 = float(prev_raw_close) / float(pre0)
                if np.isfinite(e0) and e0 > 0:
                    a = a * e0
        a = a * float(base_factor)

    for c in price_cols:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce") * a
    out["factor"] = a.to_numpy()
    return out


# ---------------------------------------------------------------- bin I/O

def _read_bin_array(path: str):
    """读取 qlib bin → (values, start_index)；文件缺失/损坏返回 (None, 0)。"""
    if not os.path.exists(path):
        return None, 0
    try:
        with open(path, "rb") as f:
            hdr = f.read(QLIB_BIN_HEADER_SIZE)
            if len(hdr) < QLIB_BIN_HEADER_SIZE:
                return None, 0
            start_index = int(round(struct.unpack("<f", hdr)[0]))
            values = np.fromfile(f, dtype=QLIB_BIN_DTYPE)
        return values, start_index
    except Exception as e:  # noqa: BLE001
        logger.warning("读取 bin 失败 %s: %s", path, e)
        return None, 0


def _write_bin_array(path: str, values: np.ndarray, start_index: int = 0) -> None:
    """原子写 qlib bin（临时文件 + os.replace），保留 start_index 头。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(struct.pack("<f", float(start_index)))
        np.asarray(values, dtype=QLIB_BIN_DTYPE).tofile(f)
    os.replace(tmp, path)


def is_stock_dir(feat_dir: str) -> bool:
    """股票目录判定：同时含 preclose/change bin（指数仅 OHLCV、ETF 无 preclose）。"""
    return (
        os.path.exists(os.path.join(feat_dir, "preclose.day.bin"))
        and os.path.exists(os.path.join(feat_dir, "change.day.bin"))
        and os.path.exists(os.path.join(feat_dir, "close.day.bin"))
    )


def rebuild_stock_hfq(feat_dir: str, apply: bool = False,
                      tol: float = CHANGE_TOL) -> dict:
    """重建单只股票 bin 为 hfq（幂等，可 dry-run）。

    Returns:
        dict: {code, ok, applied, changed, bad_indices, n, factor_max, reason}
    """
    code = os.path.basename(feat_dir.rstrip("/"))
    readings = {}
    for fld in ("close", "preclose", "change", "factor", "open", "high", "low"):
        p = os.path.join(feat_dir, f"{fld}.day.bin")
        if fld in ("preclose", "change") and not os.path.exists(p):
            return {"code": code, "ok": False, "applied": False, "changed": False,
                    "bad_indices": [], "n": 0, "factor_max": 1.0,
                    "reason": f"missing {fld}"}
        if not os.path.exists(p):
            readings[fld] = None
            continue
        vals, start = _read_bin_array(p)
        readings[fld] = (vals, start)
        if fld == "close" and (vals is None or len(vals) == 0):
            return {"code": code, "ok": False, "applied": False, "changed": False,
                    "bad_indices": [], "n": 0, "factor_max": 1.0,
                    "reason": "empty close bin"}

    close, start0 = readings["close"]
    preclose, _ = readings["preclose"]
    change, _ = readings["change"]
    n = len(close)
    for fld in ("preclose", "change"):
        vals = readings[fld][0]
        if vals is None or len(vals) != n:
            return {"code": code, "ok": False, "applied": False, "changed": False,
                    "bad_indices": [], "n": n, "factor_max": 1.0,
                    "reason": f"{fld} length mismatch"}

    factor = readings["factor"][0] if readings["factor"] else None
    if factor is not None and len(factor) != n:
        factor = None

    # 恢复原始价（幂等核心）：raw = adjusted / factor
    close = close.astype("float64")
    preclose = preclose.astype("float64")
    if factor is not None:
        f = factor.astype("float64")
        valid_f = np.isfinite(f) & (f > 0)
        raw_close = np.where(valid_f, close / f, close)
        raw_preclose = np.where(valid_f, preclose / f, preclose)
    else:
        raw_close, raw_preclose = close, preclose

    bad = validate_change_consistency(raw_close, raw_preclose, change, tol=tol)
    if bad:
        # preclose 损坏会累计污染其后所有 bar → 跳过该股，交由 repair 从 PG 重建
        return {"code": code, "ok": False, "applied": False, "changed": False,
                "bad_indices": bad[:20], "n": n, "factor_max": 1.0,
                "reason": f"change guard failed on {len(bad)} bars"}

    a = compute_hfq_factor(raw_close, raw_preclose).to_numpy()
    new = {
        "close": raw_close * a,
        "preclose": raw_preclose * a,
    }
    for fld in ("open", "high", "low"):
        vals = readings[fld][0]
        new[fld] = (vals.astype("float64") * a) if vals is not None and len(vals) == n else None

    # 计算是否发生变化（容差比较，保证幂等不反复写盘）
    changed = False
    if factor is None or len(factor) != n:
        changed = True
    elif not np.allclose(factor.astype("float64"), a, rtol=1e-6, atol=1e-9, equal_nan=True):
        changed = True
    elif not np.allclose(close, new["close"], rtol=1e-6, atol=1e-9, equal_nan=True):
        changed = True

    applied = False
    if apply and changed:
        _write_bin_array(os.path.join(feat_dir, "close.day.bin"), new["close"].astype("<f4"), start0)
        _write_bin_array(os.path.join(feat_dir, "preclose.day.bin"), new["preclose"].astype("<f4"),
                         readings["preclose"][1])
        for fld in ("open", "high", "low"):
            if new[fld] is not None:
                _write_bin_array(os.path.join(feat_dir, f"{fld}.day.bin"),
                                 new[fld].astype("<f4"), readings[fld][1])
        _write_bin_array(os.path.join(feat_dir, "factor.day.bin"), a.astype("<f4"), start0)
        applied = True

    return {"code": code, "ok": True, "applied": applied, "changed": changed,
            "bad_indices": [], "n": n, "factor_max": float(np.nanmax(a)) if n else 1.0,
            "reason": ""}


def rebuild_bins_hfq(qlib_dir: str, codes=None, skip_codes=None,
                     apply: bool = False, tol: float = CHANGE_TOL,
                     progress_cb=None) -> dict:
    """对 features/ 下全部（或指定）股票目录执行 hfq 重建。

    codes=None 时扫描 features/ 下所有 **股票目录**（含 preclose/change bin），
    自动跳过指数/ETF（仅 OHLCV 或无 preclose）。skip_codes 可显式排除。
    """
    feat_root = os.path.join(qlib_dir, "features")
    if not os.path.isdir(feat_root):
        return {"total": 0, "ok": 0, "changed": 0, "applied": 0,
                "anomalies": 0, "skipped": 0, "anomaly_samples": [], "codes": []}

    skip = set(skip_codes or set())
    if codes is not None:
        dirs = [os.path.join(feat_root, str(c).lower()) for c in codes]
    else:
        dirs = []
        for name in sorted(os.listdir(feat_root)):
            d = os.path.join(feat_root, name)
            if os.path.isdir(d) and name not in skip and is_stock_dir(d):
                dirs.append(d)

    summary = {"total": 0, "ok": 0, "changed": 0, "applied": 0,
               "anomalies": 0, "skipped": 0, "anomaly_samples": [], "codes": []}
    for i, d in enumerate(dirs):
        if not os.path.isdir(d) or os.path.basename(d) in skip:
            summary["skipped"] += 1
            continue
        if not is_stock_dir(d):
            summary["skipped"] += 1
            continue
        r = rebuild_stock_hfq(d, apply=apply, tol=tol)
        summary["total"] += 1
        if not r["ok"]:
            summary["anomalies"] += 1
            if len(summary["anomaly_samples"]) < 20:
                summary["anomaly_samples"].append(f"{r['code']}: {r['reason']}")
            continue
        summary["ok"] += 1
        if r["changed"]:
            summary["changed"] += 1
        if r["applied"]:
            summary["applied"] += 1
            summary["codes"].append(r["code"])
        if progress_cb and (i + 1) % 200 == 0:
            progress_cb(i + 1, len(dirs))
    return summary
