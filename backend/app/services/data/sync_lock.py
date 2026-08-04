"""数据爬取单实例文件锁（跨进程互斥）。

背景：
- baostock 禁止并发连接：多个爬取进程同时在线会互相拖垮，甚至触发服务端风控拉黑账号/IP。
- 上一版用「DB + 内存进度 + worker_pid」判断是否在跑，但存在竞态（pid 复用、文件残留）。

方案：用 ``fcntl.flock`` 文件排它锁。核心优势——**锁由内核持有，进程无论正常退出/
异常/被 kill -9 强杀，内核都会自动释放锁**，因此"锁"永远不会卡住，也天然保证同一时刻
只有一个爬取进程。

用法（sync_worker 入口）::

    lock = SyncLock()
    if not lock.try_acquire():
        print("已有爬取进程在运行，退出")
        return
    try:
        with baostock_session():
            ...  # 整个爬取流程
    finally:
        lock.release()
"""
import logging
import os

logger = logging.getLogger(__name__)


def _lock_path() -> str:
    try:
        from app.core.config import settings
        base = settings.PROJECT_ROOT / "data"
    except Exception:
        base = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "data")
    return os.path.join(str(base), "sync.lock")


class SyncLock:
    """基于 flock 的排它文件锁。

    try_acquire 非阻塞：
      - 成功 → 本进程持锁，返回 True
      - 失败 → 已有别的爬取进程持锁，返回 False（可直接退出）
    release 释放；进程退出（含 SIGKILL）内核自动释放。
    """

    def __init__(self, path: str = None):
        self._path = path or _lock_path()
        self._fd = None

    def try_acquire(self) -> bool:
        import fcntl
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o644)
        except OSError as e:
            logger.error("打开锁文件失败 %s: %s", self._path, e)
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        # 写入持锁进程 PID，便于排查
        try:
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode())
        except OSError:
            pass
        logger.info("爬取锁已获取 pid=%s", os.getpid())
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        import fcntl
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None
        logger.info("爬取锁已释放")


def acquire_if_free() -> SyncLock | None:
    """尝试获取爬取锁；成功返回 SyncLock，失败（已有进程在跑）返回 None。"""
    lock = SyncLock()
    if lock.try_acquire():
        return lock
    return None