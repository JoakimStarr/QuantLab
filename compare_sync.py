#!/usr/bin/env python3
"""全同步效能对比：提取指定日期的 sync.log 时间线，与 2026-08-20 旧代码基线对比。

用法（WSL）:
  .venv/bin/python3 compare_sync.py [YYYY-MM-DD]   # 默认今天

输出：各阶段耗时对比表 + 新机制日志证据清单。
"""
import json
import subprocess
import sys
from datetime import date

SYNC_LOG = "/home/joakim/QuantLab/logs/sync.log"

# 2026-08-20 旧代码基线（09:40 定时触发，总 49.5 分钟）
BASELINE = {
    "bin补齐": ("09:43:51", "09:57:54", "14.0min 串行 551,909 文件"),
    "外盘/宏观广播#1": ("09:57:58", "10:03:18", "5.5min（日历变化触发，正确）"),
    "动态成分采样": ("10:04:39", "10:19:32", "15min 56次空采样无熔断"),
    "指数同步": ("10:19:39", "10:19:39", "0ok/8fail 未走akshare兜底"),
    "ETF同步": ("10:19:39", "10:19:39", "336天全失败报'成功0失败0'"),
    "宏观/财报/外盘#2": ("10:19:39", "10:30:04", "10.5min 含广播"),
    "总计": ("09:40:44", "10:30:04", "49.5min"),
}

# 新机制预期的日志关键词 → 说明
EVIDENCE_KEYS = [
    ("动态成分缓存命中", "缓存复用成功（省 56 次采样请求）"),
    ("连续", "空采样熔断或失败计数生效"),
    ("熔断", "熔断机制触发（若因连接衰减为正常；数据正常时为误触发）"),
    ("akshare 兜底", "指数 baostock 失败后走 akshare"),
    ("跳过外盘/宏观重广播", "日历未变化时跳过重广播"),
    ("宏观字段无变化", "指纹判重跳过广播"),
    ("days-fail", "ETF 失败天数如实上报"),
    ("注意:", "完成消息携带 warnings"),
]


def fmt_delta(sec: float) -> str:
    if sec >= 60:
        return f"{sec / 60:.1f}min"
    return f"{sec:.0f}s"


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else str(date.today())
    print(f"=== {target} 全同步时间线（对比 2026-08-20 旧代码基线）===\n")

    # 提取当日 full 同步的关键事件
    keys = ["阶段", "全同步完成", "bin 文件", "日历未变化", "缓存命中", "熔断",
            "兜底", "跳过广播", "无变化", "ETF 同步进度: 33", "回填完成",
            "指数同步完成", "动态成分", "采样", "中止", "sync_worker 完成"]
    rows = []
    for line in open(SYNC_LOG, encoding="utf-8"):
        if f'"{target}T' not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        ev = d.get("event", "")
        if any(k in ev for k in keys):
            rows.append((d.get("timestamp", "")[11:19], ev))

    if not rows:
        print("当日无 full 同步日志。")
        return

    for ts, ev in rows:
        print(f"{ts} {ev[:110]}")

    print(f"\n共 {len(rows)} 条关键事件")
    print("\n=== 检查新机制证据 ===\n")
    text = "\n".join(e for _, e in rows)
    for kw, desc in EVIDENCE_KEYS:
        mark = "✓" if kw in text else " "
        print(f" [{mark}] {desc}")

    print("\n=== 基线参考（旧代码）===")
    for k, (s, e, note) in BASELINE.items():
        print(f"  {k}: {s}→{e}  {note}")


if __name__ == "__main__":
    main()
