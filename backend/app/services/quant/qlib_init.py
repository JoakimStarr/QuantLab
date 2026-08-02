"""qlib 初始化（懒加载 + 幂等）。

qlib.init 只能调用一次，本模块通过 _initialized 标志 + threading.Lock 保证幂等。
init_qlib 是同步函数，常经 run_in_executor 在多线程并发调用，故用线程锁而非 asyncio.Lock。
qlib 未安装时 init_qlib() 抛出 QlibNotAvailableError，调用方应捕获。
"""
import asyncio
import logging
import threading
from app.core.config import settings

logger = logging.getLogger(__name__)

_initialized = False
_init_lock = threading.Lock()


class QlibNotAvailableError(RuntimeError):
    """qlib 未安装或初始化失败"""


def init_qlib():
    """初始化 qlib 运行时，幂等。返回 True 表示已就绪。"""
    global _initialized
    if _initialized:
        return True
    # 仅锁住首次初始化，避免多线程并发重复调用 qlib.init（只能调一次）
    with _init_lock:
        if _initialized:
            return True
        try:
            import qlib  # noqa: F401
        except ImportError as e:
            raise QlibNotAvailableError(
                "qlib 未安装，量化功能不可用。请在 Python 3.11 环境执行: pip install pyqlib"
            ) from e

        provider_uri = settings.qlib_provider_path
        try:
            qlib.init(provider_uri=provider_uri, region="cn")
            _initialized = True
            logger.info("qlib 已初始化, provider_uri=%s", provider_uri)
            return True
        except Exception as e:
            raise QlibNotAvailableError(f"qlib 初始化失败: {e}") from e


async def is_qlib_available() -> bool:
    """探测 qlib 是否可用（不抛异常）。在线程池中执行避免阻塞事件循环。"""
    if _initialized:
        return True
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, init_qlib)
        return True
    except QlibNotAvailableError:
        return False
