"""qlib 数据目录磁盘占用：带 TTL 缓存，避免每次状态轮询都全量扫描。"""
import os
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_cache = {}  # path -> (expire_ts, size_bytes)
_CACHE_TTL = 300  # 5 分钟


def get_dir_size_bytes(path: Path) -> int:
    """目录总大小（字节）。结果缓存 5 分钟，避免重复全量扫描。"""
    key = str(path)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    size = _scan_dir(path)
    with _lock:
        _cache[key] = (time.monotonic() + _CACHE_TTL, size)
    return size


def get_dir_size_mb(path: Path) -> float:
    return round(get_dir_size_bytes(path) / (1024 * 1024), 1)


def _scan_dir(path: Path) -> int:
    total = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(path):
            for fn in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, fn))
                except OSError:
                    pass
    except OSError:
        pass
    return total
