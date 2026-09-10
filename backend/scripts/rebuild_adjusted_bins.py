"""离线重建 qlib bin 为后复权（hfq）：一次性迁移脚本，幂等、可续跑。

背景
----
历史上 bin 存的是不复权原始价 + ``factor=1.0``，qlib 会把原始价当复权价使用
（除权日伪跳空）。本脚本按 baostock 官方「涨跌幅复权法」从 bin 内已有字段
（close/preclose/change）本地重建：

    后复权因子 A_t = cumprod(前一日收盘价 / 当日前收)
    close/open/high/low/preclose ← 原始价 × A_t；factor ← A_t

幂等：读已有 ``factor``，先 ``raw = close/factor`` 还原原始价再重算，重复执行
不二次复权（容差判定，不重复写盘）。

安全
----
* **默认 dry-run**：只统计"将变化哪些股票/多少 bar"，不写盘；``--apply`` 才写。
* 应用前校验 ``|close/preclose - 1 - change| < 1e-4``：preclose 损坏会累计污染
  其后所有 bar，异常股票直接跳过（记 warning，留给 repair 从 PG 重建）。
* 写盘原子（临时文件 + ``os.replace``），保留 ``start_index`` 头与
  ``4 + 4×len(calendar)`` 长度不变式。
* ``--apply`` 时持有 ``sync_lock``（与 baostock 爬取互斥），避免在同步写 bin 期间改动。

用法（仓库根目录）
------------------
    .venv/bin/python backend/scripts/rebuild_adjusted_bins.py               # dry-run
    .venv/bin/python backend/scripts/rebuild_adjusted_bins.py --apply       # 落盘
    .venv/bin/python backend/scripts/rebuild_adjusted_bins.py --apply --codes sh600000,sz000001
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import settings  # noqa: E402
from app.services.data.data_adjusted import rebuild_bins_hfq  # noqa: E402


def _load_index_codes_sync() -> set:
    """读取 stock_index 表（指数/ETF）代码集合（小写）；无 DB 时返回空集。"""
    try:
        import asyncio

        from app.services.data.index_registry import load_index_codes
        return asyncio.run(load_index_codes())
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 读取 stock_index 失败（改为靠 preclose/change 特征排除指数/ETF）: {e}")
        return set()


def main() -> int:
    ap = argparse.ArgumentParser(description="重建 qlib bin 为后复权（hfq）")
    ap.add_argument("--apply", action="store_true",
                    help="真正写盘；缺省为 dry-run（只报告）")
    ap.add_argument("--qlib-dir", default=None, help="qlib 数据目录（默认 settings）")
    ap.add_argument("--codes", default=None,
                    help="仅处理指定股票（逗号分隔，如 sh600000,sz000001）；缺省处理全部")
    ap.add_argument("--tol", type=float, default=1e-4, help="change 一致性容差（默认 1e-4）")
    args = ap.parse_args()

    qlib_dir = args.qlib_dir or str(settings.qlib_provider_path)
    codes = [c.strip() for c in args.codes.split(",") if c.strip()] if args.codes else None

    lock = None
    if args.apply:
        # 与 baostock 爬取互斥：避免在 worker 写 bin 的同时改动
        from app.services.data.sync_lock import acquire_if_free
        lock = acquire_if_free()
        if lock is None:
            print("[error] 已有同步进程持锁（sync.lock），请待其结束后重试", file=sys.stderr)
            return 2

    try:
        skip = _load_index_codes_sync()
        mode = "APPLY（写盘）" if args.apply else "DRY-RUN（不写盘）"
        print(f"== 重建 hfq bin [{mode}] qlib_dir={qlib_dir} codes={codes or 'ALL'} ==")
        print(f"   排除指数/ETF 目录 {len(skip)} 个")

        seen = {"n": 0}

        def _cb(done, total):
            seen["n"] = done
            print(f"   ... {done}/{total}")

        summary = rebuild_bins_hfq(
            qlib_dir, codes=codes, skip_codes=skip,
            apply=args.apply, tol=args.tol, progress_cb=_cb,
        )
    finally:
        if lock is not None:
            lock.release()

    print("\n== 汇总 ==")
    print(f"  扫描股票目录 : {summary['total']}")
    print(f"  正常         : {summary['ok']}")
    print(f"  需/已变化     : {summary['changed']}")
    print(f"  已写盘       : {summary['applied']}")
    print(f"  跳过(指数/ETF/非股票) : {summary['skipped']}")
    print(f"  异常(跳过不写) : {summary['anomalies']}")
    for s in summary["anomaly_samples"]:
        print(f"    - {s}")
    if not args.apply:
        print("\n(以上为 dry-run 结果；加 --apply 落盘)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
