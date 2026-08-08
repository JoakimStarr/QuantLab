"""广播状态（broadcast fingerprint）：宏观/财报 bin 广播的跳过标记。

广播是全市场整段重写（宏观 ~51 字段 × 全市场逐股目录，财报 17 字段 × 逐股），
在"数据没变、日历没变"的补齐场景下每次重写都是浪费。用轻量状态文件记录
上次广播时的指纹（日历长度/末日期 + 源表聚合：行数、最新报告期），
指纹一致 → 跳过广播；变化（新数据/日历扩展）→ 重广播。

- fcntl.flock 排他锁保护读写：repair / 宏观手动同步 / 财报同步 多进程并发时
  不丢状态、不读到半写文件；临时文件 + os.replace 原子落盘。
- 只覆盖本模块管理的两种广播（macro / fundamental），不碰外部因素广播。
"""
import fcntl
import json
import logging
import os

logger = logging.getLogger(__name__)


def _state_path(provider_uri: str, kind: str) -> str:
    return os.path.join(provider_uri, ".broadcast_state", f"{kind}.json")


def broadcast_up_to_date(provider_uri: str, kind: str, fingerprint: dict) -> bool:
    """指纹与上次广播一致 → True（可跳过广播）；无状态/不一致 → False。"""
    p = _state_path(provider_uri, kind)
    try:
        with open(p, "r", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            state = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return False
    return state.get("fingerprint") == fingerprint


def mark_broadcast(provider_uri: str, kind: str, fingerprint: dict) -> None:
    """记录本次广播指纹（临时文件 + flock + os.replace 原子写）。"""
    p = _state_path(provider_uri, kind)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump({"fingerprint": fingerprint}, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)