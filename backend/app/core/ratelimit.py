"""限流：登录 IP 限流 + 数据源令牌桶限速。

- limiter: slowapi IP 维度限流（登录等敏感接口防爆破）
- TokenBucket / AsyncTokenBucket: 数据源调用限速（替代固定 sleep 间隔）
  令牌桶特点：平均速率稳定，允许小幅突发；比固定间隔更高效且不浪费带宽
"""
import asyncio
import os
import threading
import time

from slowapi import Limiter
from slowapi.util import get_remote_address

# 全局限流器实例，按客户端 IP 限流
limiter = Limiter(key_func=get_remote_address)


class TokenBucket:
    """同步令牌桶限速器。

    - capacity: 桶容量（允许的瞬时突发请求数）
    - rate: 每秒补充的令牌数（平均速率）

    acquire(n) 阻塞直到取得 n 个令牌；线程安全。
    """

    def __init__(self, capacity: float, rate: float):
        if capacity <= 0 or rate <= 0:
            raise ValueError("capacity 和 rate 必须为正数")
        self.capacity = float(capacity)
        self.rate = float(rate)
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._tlock = threading.Lock()

    def _refill_locked(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last_refill = now

    def acquire(self, n: float = 1.0, timeout: float = None) -> bool:
        """阻塞直到取得 n 个令牌；timeout 秒内拿不到返回 False。"""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._tlock:
            while True:
                self._refill_locked()
                if self._tokens >= n:
                    self._tokens -= n
                    return True
                # 计算还需等待多久
                need = n - self._tokens
                wait = need / self.rate
                if deadline is not None:
                    now = time.monotonic()
                    if now + wait > deadline:
                        return False
                # 释放锁后 sleep（不阻塞其他线程取令牌；这里简化为持有锁等待）
                # 注意：持有锁等待会让其他线程排队，符合限速语义
                time.sleep(wait)


class AsyncTokenBucket:
    """异步令牌桶限速器（协程友好，acquire 不阻塞事件循环）。

    - capacity: 桶容量（允许的瞬时突发请求数）
    - rate: 每秒补充的令牌数（平均速率）

    用法: await bucket.acquire()  # 取 1 个令牌
    """

    def __init__(self, capacity: float, rate: float):
        if capacity <= 0 or rate <= 0:
            raise ValueError("capacity 和 rate 必须为正数")
        self.capacity = float(capacity)
        self.rate = float(rate)
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill_locked(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last_refill = now

    async def acquire(self, n: float = 1.0, timeout: float = None) -> bool:
        """异步等待 n 个令牌；timeout 秒内拿不到返回 False。"""
        deadline = None if timeout is None else time.monotonic() + timeout
        async with self._lock:
            while True:
                self._refill_locked()
                if self._tokens >= n:
                    self._tokens -= n
                    return True
                need = n - self._tokens
                wait = need / self.rate
                if deadline is not None:
                    now = time.monotonic()
                    if now + wait > deadline:
                        return False
                # 释放锁后等待（其他协程可继续取令牌）
                # 但为保持限速语义，这里持有锁等待
                await asyncio.sleep(wait)


# ---------------- 数据源限速器单例 ----------------
# akshare 反爬严格，默认 3 req/s（容量 5 允许小幅突发）
# 可通过环境变量 AKSHARE_RATE / AKSHARE_BUCKET_CAPACITY 调整
_akshare_bucket: AsyncTokenBucket | None = None


def get_akshare_bucket() -> AsyncTokenBucket:
    """akshare 数据源限速器单例。

    默认 rate=3/s, capacity=5；可通过环境变量覆盖：
      AKSHARE_RATE: 每秒令牌补充速率
      AKSHARE_BUCKET_CAPACITY: 桶容量（突发上限）
    """
    global _akshare_bucket
    if _akshare_bucket is None:
        rate = float(os.getenv("AKSHARE_RATE", "3"))
        cap = float(os.getenv("AKSHARE_BUCKET_CAPACITY", "5"))
        _akshare_bucket = AsyncTokenBucket(capacity=cap, rate=rate)
    return _akshare_bucket
