"""进程内 TTL 缓存（轻量，单机适用）。

给热点只读接口（行情概览/K 线、因子分析）加短 TTL 缓存，避免每次请求
重复做 qlib bin 读取 / 全市场 DataFrame 计算等重活。不跨进程共享：多
worker 部署下各自独立缓存，TTL 很短，不影响一致性。

线程模型：在 FastAPI 事件循环内 get/set（请求协程），无并发写同一 key
的场景，因此不加锁；`time.monotonic` 计时避免系统时钟跳变。
"""
import time


class TTLCache:
    """有界 TTL 缓存：get 命中且未过期返回值，set 超过 maxsize 淘汰最旧一半。"""

    def __init__(self, ttl: float, maxsize: int = 64):
        if ttl <= 0 or maxsize <= 0:
            raise ValueError("ttl 与 maxsize 必须为正数")
        self.ttl = float(ttl)
        self.maxsize = int(maxsize)
        self._data: dict = {}  # key -> (insert_monotonic, value)；dict 保持插入序

    def get(self, key):
        """返回缓存值；未命中或过期返回 None（过期条目顺手清除）。"""
        item = self._data.get(key)
        if item is None:
            return None
        ts, value = item
        if time.monotonic() - ts > self.ttl:
            self._data.pop(key, None)
            return None
        return value

    def set(self, key, value) -> None:
        self._data[key] = (time.monotonic(), value)
        if len(self._data) > self.maxsize:
            # dict 按插入序迭代：淘汰最旧的一半，内存有界
            for k in list(self._data)[: self.maxsize // 2]:
                self._data.pop(k, None)

    def clear(self) -> None:
        self._data.clear()
