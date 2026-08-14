"""同步进度跟踪：纯内存模式。

设计：
- 单实例内存缓存：get_progress() 快速读取
- 线程安全：threading.Lock 保护并发更新
- 函数签名与原 Redis 版本兼容，调用方无需改动
"""
import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import AsyncGenerator, Optional

logger = logging.getLogger(__name__)


@dataclass
class SyncProgress:
    universe: str = ""
    data_source: str = ""
    # 任务归属 kind：backfill/eod/repair/indices/fundamental/macro/etf/full。
    # 与 data_source 的区别：data_source 是"真实数据源"（baostock/eastmoney/...）或 full
    # 的阶段性标识；kind 稳定标识"谁触发的任务"，前端据此区分展示（如 Macro 页匹配 kind=macro）。
    kind: str = ""
    status: str = "idle"  # idle / downloading / extracting / verifying / done / failed
    progress_pct: float = 0.0
    downloaded_mb: float = 0.0
    total_mb: float = 0.0
    speed_mbps: float = 0.0
    started_at: Optional[str] = None
    message: str = ""
    error: Optional[str] = None
    # 独立 worker 子进程的 PID：用于 web 进程检测同步是否真的在跑（避免残留 syncing 僵尸）
    worker_pid: Optional[int] = None
    # 是否写 qlib bin：True=回填/补齐/指数/EOD/广播（读 bin 的操作应被阻塞）；
    # False=fetch-only 任务（如财报拉取只写 PG），不影响 bin 读取，可并行
    writes_bins: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _progress_file() -> str:
    """共享进度文件路径（独立 worker 子进程与 web 进程之间的进度桥梁）。

    放在项目 data/ 目录下，跨进程可读写。
    """
    try:
        from app.core.config import settings
        base = settings.PROJECT_ROOT / "data"
    except Exception:
        base = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "data")
    return os.path.join(str(base), "sync_progress.json")


def _read_progress_file() -> Optional[dict]:
    path = _progress_file()
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _write_progress_file(progress: Optional[dict]) -> None:
    path = _progress_file()
    try:
        if progress is None:
            if os.path.exists(path):
                os.remove(path)
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(progress, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:
        logger.debug("写入进度文件失败: %s", e)


def _pid_alive(pid) -> bool:
    """判断 PID 对应的进程是否存活（僵尸进程视为已死）。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 存在但无权限探测（如其他用户进程），保守视为存活
    except OSError:
        return False
    # kill(pid,0) 成功不代表进程真的在跑：僵尸进程（defunct）仍保留进程表项。
    # 僵尸已不执行任何代码，若 worker 异常退出且无人 waitpid 回收，残留的进度
    # 文件会让 sync_is_active 长期误判"正在同步"而阻塞后续同步/补齐（409）。
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="ascii") as f:
            state = f.read().split()[2]
        return state != "Z"
    except (OSError, IndexError, ValueError):
        return True  # /proc 不可用或解析失败时退化为仅靠 kill 探测


class SyncProgressManager:
    """纯内存进度管理器（线程安全）。

    单实例使用，进度存储在进程内存中。
    若未来需要多实例进度共享，可重新引入 Redis Pub/Sub。
    """

    def __init__(self, **_kwargs):
        # 兼容旧调用签名 SyncProgressManager(redis_url=..., enabled=...)，参数忽略
        self._lock = threading.Lock()
        self._progress: Optional[SyncProgress] = None

    def init_progress(self, universe: str, data_source: str, total_mb: float = 0,
                      writes_bins: bool = False, kind: str = None) -> None:
        """初始化进度跟踪

        writes_bins: 该任务是否写 qlib bin（读 bin 的操作应被阻塞）；
            fetch-only 任务传 False，允许挖掘/校验等读 bin 操作并行。
        kind: 任务归属（backfill/eod/repair/...），None 时回退用 data_source。
        """
        with self._lock:
            # 保留仍存活的 worker_pid：独立 worker 子进程内任务可能再次
            # init_progress（如 run_repair / run_baostock_backfill），若丢失 pid，
            # 进程被杀后 sync_is_active 将无法识别僵尸任务而长期阻塞重触发。
            alive_pid = None
            prev = self._progress
            if prev is not None and _pid_alive(prev.worker_pid):
                alive_pid = prev.worker_pid
            elif prev is None:
                f = _read_progress_file()
                if f and _pid_alive(f.get("worker_pid")):
                    alive_pid = f["worker_pid"]
            self._progress = SyncProgress(
                universe=universe,
                data_source=data_source,
                kind=kind or data_source,
                status="downloading",
                total_mb=total_mb,
                started_at=datetime.now().isoformat(),
                writes_bins=writes_bins,
            )
            if alive_pid:
                self._progress.worker_pid = int(alive_pid)
            _write_progress_file(self._progress.to_dict())

    def update_progress(
        self,
        pct: float = None,
        downloaded_mb: float = None,
        speed_mbps: float = None,
        status: str = None,
        message: str = None,
        error: str = None,
    ) -> None:
        """更新进度"""
        with self._lock:
            if self._progress is None:
                return
            if pct is not None:
                self._progress.progress_pct = round(pct, 1)
            if downloaded_mb is not None:
                self._progress.downloaded_mb = round(downloaded_mb, 1)
            if speed_mbps is not None:
                self._progress.speed_mbps = round(speed_mbps, 1)
            if status is not None:
                self._progress.status = status
            if message is not None:
                self._progress.message = message
            if error is not None:
                self._progress.error = error
            _write_progress_file(self._progress.to_dict())

    def finish_progress(self, success: bool, error: str = None) -> None:
        """完成进度"""
        with self._lock:
            if self._progress is None:
                return
            self._progress.status = "done" if success else "failed"
            self._progress.progress_pct = 100.0 if success else self._progress.progress_pct
            self._progress.error = error
            _write_progress_file(self._progress.to_dict())

    def get_progress(self) -> Optional[dict]:
        """获取当前进度。

        优先取进程内存；内存为空时回退到共享进度文件（独立 worker 子进程的进度）。
        """
        with self._lock:
            if self._progress is not None:
                return self._progress.to_dict()
            file_prog = _read_progress_file()
            if file_prog is not None:
                return file_prog
            return None

    def clear_progress(self) -> None:
        """清除进度"""
        with self._lock:
            self._progress = None
            _write_progress_file(None)

    async def subscribe_progress(self) -> AsyncGenerator[dict, None]:
        """订阅进度更新（内存轮询模式）。

        每 0.5s 轮询一次内存进度，yield 进度字典。
        供 WebSocket 等长期连接使用。完成后自动结束。
        """
        last_pct = -1
        last_status = None
        while True:
            progress = self.get_progress()
            if progress is None:
                await asyncio.sleep(0.5)
                continue
            # 只在进度变化时 yield，避免重复推送
            if progress["progress_pct"] != last_pct or progress["status"] != last_status:
                last_pct = progress["progress_pct"]
                last_status = progress["status"]
                yield progress
            # done/failed 后结束订阅
            if progress["status"] in ("done", "failed"):
                break
            await asyncio.sleep(0.5)

    async def close(self) -> None:
        """关闭管理器（内存模式无资源需释放，空操作）。"""
        pass


# -- 全局单例 --

_manager: Optional[SyncProgressManager] = None


def _get_manager() -> SyncProgressManager:
    """获取或初始化全局进度管理器（惰性初始化）。"""
    global _manager
    if _manager is None:
        _manager = SyncProgressManager()
    return _manager


# -- 模块级函数（向后兼容，保持原有签名） --


def init_progress(universe: str, data_source: str, total_mb: float = 0,
                  writes_bins: bool = False, kind: str = None) -> None:
    """初始化进度跟踪"""
    _get_manager().init_progress(universe, data_source, total_mb, writes_bins=writes_bins, kind=kind)


def update_progress(
    pct: float = None,
    downloaded_mb: float = None,
    speed_mbps: float = None,
    status: str = None,
    message: str = None,
    error: str = None,
) -> None:
    """更新进度"""
    _get_manager().update_progress(
        pct=pct,
        downloaded_mb=downloaded_mb,
        speed_mbps=speed_mbps,
        status=status,
        message=message,
        error=error,
    )


def finish_progress(success: bool, error: str = None) -> None:
    """完成进度"""
    _get_manager().finish_progress(success, error)


def get_progress() -> Optional[dict]:
    """获取当前进度"""
    return _get_manager().get_progress()


def set_worker_pid(pid: int) -> None:
    """记录运行在独立子进程里的 worker PID（供 web 进程检测存活）。"""
    manager = _get_manager()
    with manager._lock:
        if manager._progress is None:
            return
        manager._progress.worker_pid = int(pid)
        _write_progress_file(manager._progress.to_dict())


def sync_is_active() -> bool:
    """判断当前是否真的有一个活跃的同步任务在跑。

    - 内存进度存在且状态非终态 → 活跃
    - 共享文件进度存在：
        - 状态为终态(done/failed) → 不活跃（等被 clear）
        - 有 worker_pid 且存活 → 活跃
        - 有 worker_pid 但已死（残留文件）→ 不活跃，允许重新触发并提示清理
        - 无 worker_pid 且状态非终态 → 保守视为活跃（历史 in-process 路径）
    """
    progress = get_progress()
    if progress is None:
        return False
    if progress.get("status") in ("done", "failed", "idle", None):
        return False
    pid = progress.get("worker_pid")
    if pid and not _pid_alive(pid):
        # worker 已死，残留进度文件 → 不活跃，允许重新触发
        return False
    return True


def writes_bins_active() -> bool:
    """是否存在会写 qlib bin 的活跃同步任务（回填/补齐/指数/EOD/广播）。

    fetch-only 任务（如财报拉取只写 PG，writes_bins=False）返回 False，
    不阻塞挖掘/校验等读 bin 的操作。
    """
    if not sync_is_active():
        return False
    progress = get_progress()
    return bool(progress and progress.get("writes_bins"))


def ensure_no_bin_sync(waiting_task: str = "", suffix: str = "") -> None:
    """写 bin 同步活跃时抛 409，阻止并发触发新同步。

    供各同步端点统一调用（替代 8 处重复的
    ``if writes_bins_active(): return ApiResponse(ok=False, error={SYNC_IN_PROGRESS})``）。
    """
    if writes_bins_active():
        from app.core.errors import AppError
        raise AppError("SYNC_IN_PROGRESS", busy_message(waiting_task) + suffix, 409)


# 会重塑 day.txt 对齐（历史前置扩展/重建日历）的同步 kind——此类同步期间读 bin
# 可能读到错位数据（bin 已按新日历重排而 day.txt 未更新），回测/挖掘必须等待。
# 纯追加/回填的同步（eod/etf/indices，bin 原子写后对齐前缀，读侧安全）不在此列。
_CALENDAR_SHIFTING_KINDS = {"baostock", "repair", "full"}


def calendar_shifting_active() -> bool:
    """是否存在会重塑日历对齐的活跃同步（回填历史扩展/补齐重建）。

    与 writes_bins_active 的区别：后者拦截"任何写 bin"（用于同步之间串行），
    本函数只拦截"可能让读者读到错位数据"的同步，用于回测/挖掘的并发放行——
    EOD/ETF/指数等纯追加同步写 bin 时，回测可正常进行（bin 原子写保证不读到半成品）。
    """
    if not sync_is_active():
        return False
    progress = get_progress()
    if not (progress and progress.get("writes_bins")):
        return False
    source = (progress.get("data_source") or "").lower()
    return source in _CALENDAR_SHIFTING_KINDS


def clear_progress() -> None:
    """清除进度"""
    _get_manager().clear_progress()


# 任务类型 → 可读标签（与前端 taskLabel 保持一致）
_TASK_LABEL = {
    "baostock": "baostock 全量回填",
    "backfill": "baostock 全量回填",
    "eod": "增量同步",
    "repair": "数据补齐",
    "indices": "指数同步",
    "etf": "ETF 同步",
    "macro": "宏观同步",
    "eastmoney": "宏观同步",
    "global_macro": "全球宏观同步",
    "fundamental": "财报同步",
    "full": "一键全同步",
}


def busy_message(waiting_task: str = "") -> str:
    """生成"同步忙"的友好提示。

    说明当前活跃任务是谁（data_source + universe）、进度多少；
    waiting_task="repair" 时额外说明为何补齐也必须等（与回填写同一批
    qlib bin/day.txt 文件，串行是为了避免并发写坏 bin）。
    """
    progress = get_progress()
    if not progress:
        return "正在同步/修复中，请稍候"
    universe = progress.get("universe") or "?"
    source = progress.get("kind") or progress.get("data_source") or "同步"
    label = _TASK_LABEL.get(source, source)
    pct = progress.get("progress_pct")
    pct_str = f"，进度 {pct:.0f}%" if isinstance(pct, (int, float)) else ""
    msg = f"正在同步/修复中，请稍候（当前任务: {label}，universe={universe}{pct_str}）"
    if waiting_task == "repair":
        msg += "；补齐会写入同一批 qlib bin/day.txt 文件，需等当前任务完成后才能执行"
    return msg


async def subscribe_progress() -> AsyncGenerator[dict, None]:
    """订阅进度更新（异步生成器，内存轮询模式）。"""
    async for msg in _get_manager().subscribe_progress():
        yield msg


async def close_progress_manager() -> None:
    """关闭进度管理器（内存模式无资源需释放，空操作）。"""
    global _manager
    if _manager is not None:
        await _manager.close()
        _manager = None
