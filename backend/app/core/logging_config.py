"""日志系统统一配置：JSON 格式（structlog）+ 跨进程安全轮转 + 动态级别调整。

设计（统一日志系统，web 进程与 sync worker 子进程共用同一份配置入口 setup_logging）：
- 统一 JSON 结构化日志格式：structlog 驱动，{"timestamp", "level", "logger", "event",
  "module", "function", "line", "pid", "request_id"?, "worker_kind"?, "exception"?}
- 3 个日志文件：
    quantlab.log   web 进程全量日志（INFO+，100MB×5，备份保留 7 天）
    error.log      web 进程 WARNING+（100MB×5，备份保留 15 天，供回溯历史错误）
    sync.log       全部 sync worker 子进程（JSON 行内 worker_kind 字段区分任务类型）
- 日志轮转：LockedRotatingFileHandler（fcntl flock 串行化跨进程写入/轮转）
- 日志级别动态调整：通过 API 端点 /logs/level（PUT）实时调整，运行时生效、重启恢复
- request_id / worker_kind 注入：通过 contextvars 经由 structlog 处理器注入
"""
import contextvars
import logging
import logging.config
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog

try:
    import fcntl
except ImportError:  # pragma: no cover - 非 POSIX 平台
    fcntl = None


# 请求级上下文变量，由中间件设置，由 structlog 处理器 / 错误处理器读取
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
# 同步 worker 上下文变量（worker_kind 注入到日志行，区分任务类型）
worker_kind_var: contextvars.ContextVar[str] = contextvars.ContextVar("worker_kind", default="")
# 日志目录，由 logs API 路由引用
log_dir: Path = Path("logs")


class LockedRotatingFileHandler(RotatingFileHandler):
    """跨进程安全的轮转文件 handler（fcntl flock）。

    sync.log 可能被多个 worker 进程并发写入（baostock 系列由爬取锁串行，
    但 fundamental/macro 等 akshare/eastmoney 任务不与爬取锁互斥），标准
    RotatingFileHandler 的轮转（rename 当前文件）在跨进程时存在竞态：
    两个进程可能同时触发 doRollover 导致文件丢失。

    用 <filename>.lock 侧车文件 + flock 把整个 emit（含可能的轮转）串行化。
    logging 内置的 handler 锁已保证单进程内线程安全，flock 额外覆盖跨进程。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._flock_fd = None
        if fcntl is not None:
            try:
                self._flock_fd = open(f"{self.baseFilename}.lock", "a")  # noqa: SIM115
            except OSError:  # pragma: no cover
                self._flock_fd = None

    def emit(self, record):
        if self._flock_fd is None:
            super().emit(record)
            return
        try:
            fcntl.flock(self._flock_fd.fileno(), fcntl.LOCK_EX)
            try:
                super().emit(record)
                self._flock_fd.flush()
            finally:
                fcntl.flock(self._flock_fd.fileno(), fcntl.LOCK_UN)
        except OSError:  # pragma: no cover - 锁失败时退化为普通写入
            super().emit(record)

    def close(self):
        if self._flock_fd is not None:
            try:
                self._flock_fd.close()
            except OSError:  # pragma: no cover
                pass
            self._flock_fd = None
        super().close()


def _extra_fields_processor(logger, method_name, event_dict):
    """将 extra_fields 展平到事件字典中（兼容中间件 perf_logger 调用）。"""
    # 处理来自标准库 Logger 的 LogRecord 属性
    record = event_dict.get("_record")
    if record is not None:
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            event_dict.update(extra)
    # 处理来自 structlog logger 的额外字段
    extra = event_dict.pop("extra_fields", None)
    if isinstance(extra, dict):
        event_dict.update(extra)
    return event_dict


def _request_id_processor(logger, method_name, event_dict):
    """从 contextvars 注入 request_id 到日志事件。"""
    rid = request_id_var.get("")
    if rid:
        event_dict["request_id"] = rid
    return event_dict


def _add_pid(logger, method_name, event_dict):
    """注入进程 PID（区分 web/worker 进程，排查并发与子进程问题）。"""
    event_dict["pid"] = os.getpid()
    return event_dict


def _add_worker_kind(logger, method_name, event_dict):
    """从 contextvars 注入 worker_kind（sync worker 子进程设置）。"""
    kind = worker_kind_var.get("")
    if kind:
        event_dict["worker_kind"] = kind
    return event_dict


def set_worker_kind(kind: str) -> None:
    """为当前上下文设置 worker_kind（sync worker 子进程入口调用）。"""
    worker_kind_var.set(kind)


def _add_logger_name(logger, method_name, event_dict):
    """添加日志器名称。"""
    record = event_dict.get("_record")
    if record is not None:
        event_dict["logger"] = record.name
    return event_dict


def _add_caller_info(logger, method_name, event_dict):
    """添加调用者信息（模块、函数、行号）。"""
    record = event_dict.get("_record")
    if record is not None:
        event_dict["module"] = record.module
        event_dict["function"] = record.funcName
        event_dict["line"] = record.lineno
    return event_dict


def setup_logging(log_dir: str = "logs", level: str = "INFO", json_format: bool = True,
                  console: bool = True, log_file: str = "quantlab.log",
                  error_file: str | None = "error.log") -> None:
    """配置日志系统（web 进程与 sync worker 子进程共用入口）。

    Args:
        log_dir: 日志目录（相对于项目根或绝对路径）
        level: 日志级别
        json_format: True 使用 JSON 格式（structlog JSONRenderer），
                     False 使用 ConsoleRenderer（文本格式）
        console: True 时同时输出到 stdout（开发实时可见）。
                 文件日志始终保留，是 UI 与归档的来源。
        log_file: 主日志文件名（web: quantlab.log；worker: sync.log）
        error_file: WARNING+ 错误日志文件名；None 则只写主文件（worker 用，
                    避免每个 worker 各建一份 error 文件）
    """
    log_path = Path(log_dir)
    if not log_path.is_absolute():
        # 相对路径一律相对项目根解析（与 config.py 同款推导 parents[3]），
        # 避免因启动 CWD 不同导致日志写入目录与 logs API 读取目录错位。
        root = os.environ.get("PROJECT_ROOT") or Path(__file__).resolve().parents[3]
        log_path = Path(root) / log_path
    log_path.mkdir(parents=True, exist_ok=True)
    # 更新模块级变量，供 logs API 路由使用
    globals()["log_dir"] = log_path

    # 共享处理器链：structlog 日志器与标准库日志器共用
    pre_chain = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.format_exc_info,  # 把 exc_info 元组格式化为可读 traceback（输出到 exception 键）
        _extra_fields_processor,
        _request_id_processor,
        _add_worker_kind,
        _add_pid,
        _add_logger_name,
        _add_caller_info,
    ]

    # 配置 structlog
    structlog.configure(
        processors=pre_chain + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # 选择最终渲染器
    if json_format:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    formatters = {
        "structlog": {
            "()": structlog.stdlib.ProcessorFormatter,
            "processor": renderer,
            "foreign_pre_chain": pre_chain,
        },
    }

    handlers = {
        "file": {
            "class": "app.core.logging_config.LockedRotatingFileHandler",
            "filename": str(log_path / log_file),
            "maxBytes": 100 * 1024 * 1024,  # 100MB
            "backupCount": 5,
            "encoding": "utf-8",
            "formatter": "structlog",
        },
    }
    if error_file:
        handlers["error_file"] = {
            "class": "app.core.logging_config.LockedRotatingFileHandler",
            "filename": str(log_path / error_file),
            "maxBytes": 100 * 1024 * 1024,  # 100MB
            "backupCount": 5,
            "encoding": "utf-8",
            "level": "WARNING",
            "formatter": "structlog",
        }
    if console:
        handlers["console"] = {
            "class": "logging.StreamHandler",
            "stream": sys.stdout,
            "formatter": "structlog",
        }

    # 显式管理的第三方 logger：让 uvicorn 启动/重载日志落盘、压制噪音刷屏。
    # 注意：uvicorn 自身的 configure_logging 会在应用启动前覆盖一次，setup_logging
    # 在 lifespan 中执行、晚于它，因此这里的配置最终生效。
    loggers = {
        "uvicorn": {"handlers": [], "level": "INFO", "propagate": True},
        "uvicorn.error": {"handlers": [], "level": "INFO", "propagate": True},
        # 每请求一条的 access 日志由 perf 日志替代，调到 WARNING 只留异常
        "uvicorn.access": {"handlers": [], "level": "WARNING", "propagate": True},
        # asgi_correlation_id 在请求头校验失败时每请求刷一条 WARNING，曾淹没 error.log
        "asgi_correlation_id": {"handlers": [], "level": "ERROR", "propagate": True},
        # apscheduler 每次 add_job 刷多条 INFO
        "apscheduler": {"handlers": [], "level": "WARNING", "propagate": True},
    }

    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": formatters,
        "handlers": handlers,
        "loggers": loggers,
        "root": {
            "level": level.upper(),
            "handlers": (["file"] + (["error_file"] if error_file else [])
                         + (["console"] if console else [])),
        },
    }

    logging.config.dictConfig(config)
    logging.getLogger(__name__).info(
        "日志系统已初始化: level=%s, json_format=%s, dir=%s, log_file=%s, error_file=%s, console=%s",
        level, json_format, log_path, log_file, error_file, console)
    # 启动时确认受管 logger 实际级别（uvicorn 先于 lifespan 配置过日志，这里做最终校验）
    logging.getLogger(__name__).info(
        "受管 logger 级别: %s",
        {n: logging.getLevelName(logging.getLogger(n).level) for n in _MANAGED_LOGGERS})


# 显式配置了独立级别的第三方 logger：动态调级时需与 root 一起调整，
# 否则（如切到 DEBUG 排查时）它们仍被各自的 WARNING/ERROR 级别挡在门外
_MANAGED_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access",
                    "asgi_correlation_id", "apscheduler")


def set_log_level(level: str) -> None:
    """动态调整日志级别（运行时生效，重启恢复为 config 默认）。

    同时调整 root 与 _MANAGED_LOGGERS，保证 DEBUG 排查时全链路生效。
    前端通过 /logs/level（PUT）调用。
    """
    level = level.upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ValueError(f"无效的日志级别: {level}")
    for name in ("", *_MANAGED_LOGGERS):
        logging.getLogger(name).setLevel(level)
    logging.getLogger(__name__).info("日志级别已调整为: %s", level)


# 清理时的备份文件后缀模式 -> 保留天数（普通日志短、错误日志长）。
# 只匹配数字后缀（RotatingFileHandler 备份为 .1/.2/...），避免误删 .lock 锁文件。
_CLEANUP_PATTERNS = {
    "quantlab.log.[0-9]*": "retention_days",
    "error.log.[0-9]*": "error_retention_days",
    "sync.log.[0-9]*": "retention_days",
}


def cleanup_old_logs(log_dir: str | Path = None,
                     retention_days: int = 7,
                     error_retention_days: int = 15) -> dict:
    """删除过期的日志轮转备份，保留足够长的错误日志用于定位问题。

    规则：
    - 只清理带轮转后缀的备份文件（quantlab.log.1、error.log.2、sync.log.1 等），
      当前正在写入的文件（无后缀）永不删除
    - 普通日志（quantlab.log/sync.log 备份）保留 retention_days 天
    - 错误日志（error.log 备份）保留 error_retention_days 天（更长），
      保证普通日志清掉后仍能回溯历史错误与 traceback
    - 错误日志内容同时可由 /logs API 按 level=ERROR 检索
    - 用 `{log_dir}/.cleanup.lock` 文件锁防并发：多实例/重复触发时
      后到的实例直接跳过，避免并发删除

    Args:
        log_dir: 日志目录（默认使用 setup_logging 设置的全局目录）
        retention_days: 普通日志备份保留天数
        error_retention_days: 错误日志备份保留天数

    Returns:
        dict: {deleted: [文件名], freed_bytes: int, deleted_count: int,
               skipped: bool}（skipped=True 表示有其他实例正在清理）
    """
    import time

    log_path = Path(log_dir) if log_dir else log_dir_global()
    if not log_path.is_dir():
        return {"deleted": [], "freed_bytes": 0, "deleted_count": 0, "skipped": False}

    lock = _acquire_cleanup_lock(log_path)
    if lock is None:
        logging.getLogger(__name__).warning("日志清理被跳过：其他实例正在执行（%s）", log_path)
        return {"deleted": [], "freed_bytes": 0, "deleted_count": 0, "skipped": True}
    try:
        now = time.time()
        deleted: list[str] = []
        freed_bytes = 0
        for pattern, key in _CLEANUP_PATTERNS.items():
            keep_days = error_retention_days if key == "error_retention_days" else retention_days
            for fp in sorted(log_path.glob(pattern)):
                try:
                    age_days = (now - fp.stat().st_mtime) / 86400.0
                except OSError:
                    continue
                if age_days > keep_days:
                    try:
                        freed_bytes += fp.stat().st_size
                        fp.unlink()
                        deleted.append(str(fp.name))
                    except OSError:
                        continue
        if deleted:
            logging.getLogger(__name__).info(
                "日志清理: 删除 %d 个过期备份，释放 %.2f MB: %s",
                len(deleted), freed_bytes / 1048576.0, ",".join(deleted))
        return {"deleted": deleted, "freed_bytes": freed_bytes,
                "deleted_count": len(deleted), "skipped": False}
    finally:
        _release_cleanup_lock(lock)


def _acquire_cleanup_lock(log_path: Path):
    """获取清理互斥锁（非阻塞 flock）。返回文件对象，失败返回 None。

    多实例部署时防止两个进程同时删除同一批文件。
    """
    try:
        import fcntl
    except ImportError:  # 非 POSIX 平台（如 Windows）退化为无锁
        return None
    lock_path = log_path / ".cleanup.lock"
    try:
        f = open(lock_path, "w")
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        try:
            if "f" in locals():
                f.close()
        except OSError:
            pass
        return None


def _release_cleanup_lock(f) -> None:
    """释放清理锁并删除锁文件。"""
    try:
        import fcntl
        fcntl.flock(f, fcntl.LOCK_UN)
    except Exception:  # noqa: BLE001
        pass
    try:
        f.close()
        if f.name and Path(f.name).exists():
            Path(f.name).unlink(missing_ok=True)
    except OSError:
        pass


def log_dir_global() -> Path:
    """返回当前日志目录（兼容未走 setup_logging 的调用）。"""
    return globals().get("log_dir", Path("logs"))
