"""任务执行器：分离 IO 线程池与 CPU 进程池，避免 CPU 密集任务拖垮事件循环。

设计：
- io_executor: ThreadPoolExecutor，用于同步 IO（akshare/qlib 数据读取）
- cpu_executor: ProcessPoolExecutor，用于纯函数 CPU 密集任务（因子评价、CV 训练）
  绕过 GIL 真并行；要求被调用函数与参数可 pickle（模块级函数即可）

worker 数从 config.task 读取，缺省 cpu=min(4, cpu_count), io=8。
"""
import os
import logging
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from typing import Callable, TypeVar

from app.core.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

_io_executor: ThreadPoolExecutor | None = None
_cpu_executor: ProcessPoolExecutor | None = None


def get_io_executor() -> ThreadPoolExecutor:
    """IO 线程池单例（akshare/qlib 等同步 IO）。"""
    global _io_executor
    if _io_executor is None:
        n = int((settings.task or {}).get("io_workers", 8))
        _io_executor = ThreadPoolExecutor(max_workers=n, thread_name_prefix="ql-io")
        logger.info("IO 线程池初始化: max_workers=%d", n)
    return _io_executor


def get_cpu_executor() -> ProcessPoolExecutor:
    """CPU 进程池单例（因子评价/CV 训练等纯函数 CPU 任务）。"""
    global _cpu_executor
    if _cpu_executor is None:
        n = int((settings.task or {}).get("cpu_workers", max(2, (os.cpu_count() or 4) // 2)))
        _cpu_executor = ProcessPoolExecutor(max_workers=n)
        logger.info("CPU 进程池初始化: max_workers=%d", n)
    return _cpu_executor


async def run_cpu(func: Callable[..., T], *args, **kwargs) -> T:
    """在 CPU 进程池中运行纯函数，返回结果。

    使用约束：
    - func 必须是模块级函数（可 pickle），不能是闭包/lambda
    - args/kwargs 必须可 pickle
    - 适合 evaluate_factor / time_series_cv_eval 等无状态计算
    """
    import asyncio
    loop = asyncio.get_running_loop()
    if kwargs:
        from functools import partial
        return await loop.run_in_executor(get_cpu_executor(), partial(func, *args, **kwargs))
    return await loop.run_in_executor(get_cpu_executor(), func, *args)


async def run_io_cpu(func: Callable[..., T], *args, **kwargs) -> T:
    """在 IO 线程池中运行释放 GIL 的 CPU 任务（如 qlib 因子评价）。

    因子评价主要调用 qlib C 扩展，会释放 GIL，线程池足够且无序列化开销。
    相比 run_cpu 的 ProcessPoolExecutor，避免 pickle 序列化与跨进程数据拷贝。
    """
    import asyncio
    loop = asyncio.get_running_loop()
    if kwargs:
        from functools import partial
        return await loop.run_in_executor(get_io_executor(), partial(func, *args, **kwargs))
    return await loop.run_in_executor(get_io_executor(), func, *args)


async def run_mixed(func: Callable[..., T], *args, is_cpu_bound: bool = False, **kwargs) -> T:
    """根据任务类型自动选择线程池/进程池。

    Args:
        func: 待执行函数
        is_cpu_bound: True 走进程池（ProcessPoolExecutor），False 走线程池（ThreadPoolExecutor）
    """
    if is_cpu_bound:
        return await run_cpu(func, *args, **kwargs)
    return await run_io_cpu(func, *args, **kwargs)


def shutdown_executors() -> None:
    """应用关闭时清理池。"""
    global _io_executor, _cpu_executor
    if _io_executor is not None:
        _io_executor.shutdown(wait=False)
        _io_executor = None
    if _cpu_executor is not None:
        _cpu_executor.shutdown(wait=False)
        _cpu_executor = None
