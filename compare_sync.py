#!/usr/bin/env python3
"""全同步效能对比：提取指定日期 sync.log 的每次全同步运行，与 2026-08-20 旧代码基线对比。

用法（WSL）:
  .venv/bin/python3 compare_sync.py [YYYY-MM-DD]        # 默认今天
  .venv/bin/python3 compare_sync.py 2026-08-20 2        # 只看第 2 次运行

同日多次运行（如早晚各一次）自动分段：检测"阶段1/6"起始标记。
注意：sync.log 时间戳为 UTC，比北京时间晚 8 小时。
"""
import json
import sys
from datetime import date

SYNC_LOG = "/home/joakim/QuantLab/logs/sync.log"

# 2026-08-20 旧代码基线（09:40 CST 定时触发，总 49.5 分钟）
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
    ("熔断", "熔断机制触发（因连接衰减触发为正常；数据正常时为误触发）"),
    ("akshare 兜底", "指数 baostock 失败后走 akshare"),
    ("跳过外盘/宏观重广播", "日历未变化时跳过重广播"),
    ("宏观字段无变化", "指纹判重跳过广播"),
    ("days-fail", "ETF 失败天数如实上报"),
    ("注意:", "完成消息携带 warnings"),
]

KEYS = ["sync_worker", "阶段", "全同步完成", "交易日数", "已按新日历补齐",
        "重新对齐广播", "广播", "日历未变化", "缓存命中", "熔断", "兜底",
        "跳过", "无变化", "回填完成", "指数同步完成", "动态成分", "采样",
        "中止", "days-fail", "重建日历"]
# 噪音过滤：逐字段广播/逐 ETF 进度行
NOISE = ["广播写入", "ETF 同步进度"]


def load_runs(target: str) -> list[list[tuple[str, str]]]:
    """按"sync_worker 开始: kind=full"起始标记把当日日志切成多次运行。"""
    runs: list[list[tuple[str, str]]] = []
    for line in open(SYNC_LOG, encoding="utf-8"):
        if f'"{target}T' not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        ev = d.get("event", "")
        if not any(k in ev for k in KEYS) or any(n in ev for n in NOISE):
            continue
        row = (d.get("timestamp", "")[11:19], ev)
        if "sync_worker 开始: kind=full" in ev:
            runs.append([row])
        elif runs:
            runs[-1].append(row)
    return runs


def show_run(idx: int, rows: list[tuple[str, str]]) -> None:
    print(f"--- 运行 #{idx}（{len(rows)} 条关键事件）---")
    for ts, ev in rows:
        print(f"{ts} {ev[:110]}")
    text = "\n".join(e for _, e in rows)
    print("新机制证据:")
    for kw, desc in EVIDENCE_KEYS:
        mark = "✓" if kw in text else "·"
        print(f"  [{mark}] {desc}")
    print()


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else str(date.today())
    only = int(sys.argv[2]) if len(sys.argv) > 2 else None
    print(f"=== {target} 全同步运行（对比 2026-08-20 旧代码基线 49.5min）===")
    print("注：日志时间为 UTC（北京时间 -8h）\n")

    runs = load_runs(target)
    if not runs:
        print("当日无全同步日志。")
        return

    for i, rows in enumerate(runs, 1):
        if only is None or i == only:
            show_run(i, rows)

    print("=== 基线参考（2026-08-20 09:40 CST 旧代码）===")
    for k, (s, e, note) in BASELINE.items():
        print(f"  {k}: {s}→{e}  {note}")


if __name__ == "__main__":
    main()
