"""日志系统结构化配置：JSON 格式（structlog）+ 日志轮转 + 动态级别调整。

设计：
- 统一 JSON 结构化日志格式：structlog 驱动，{"timestamp", "level", "logger", "message", "request_id", ...}
- 日志轮转：RotatingFileHandler（最大 100MB，保留 5 份）
- 日志级别动态调整：通过 API 端点 /api/v1/admin/log-level 实时调整
- request_id 注入：通过 contextvars 经由 structlog 处理器注入
"""
import contextvars
import logging
import logging.config
import os
import sys
from pathlib import Path

import structlog


# 请求级上下文变量，由中间件设置，由 structlog 处理器 / 错误处理器读取
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
# 日志目录，由 logs API 路由引用
log_dir: Path = Path("logs")


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
                  console: bool = True) -> None:
    """配置根日志器。

    Args:
        log_dir: 日志目录（相对于项目根或绝对路径）
        level: 日志级别
        json_format: True 使用 JSON 格式（structlog JSONRenderer），
                     False 使用 ConsoleRenderer（文本格式）
        console: True 时同时输出到 stdout（开发实时可见）。
                 文件日志（quantlab.log / error.log）始终保留，是 UI 与归档的来源。
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
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(log_path / "quantlab.log"),
            "maxBytes": 100 * 1024 * 1024,  # 100MB
            "backupCount": 5,
            "encoding": "utf-8",
            "formatter": "structlog",
        },
        "error_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(log_path / "error.log"),
            "maxBytes": 100 * 1024 * 1024,  # 100MB
            "backupCount": 5,
            "encoding": "utf-8",
            "level": "WARNING",
            "formatter": "structlog",
        },
    }
    if console:
        handlers["console"] = {
            "class": "logging.StreamHandler",
            "stream": sys.stdout,
            "formatter": "structlog",
        }

    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": formatters,
        "handlers": handlers,
        "root": {
            "level": level.upper(),
            "handlers": ["file", "error_file"] + (["console"] if console else []),
        },
    }

    logging.config.dictConfig(config)
    logging.getLogger(__name__).info(
        "日志系统已初始化: level=%s, json_format=%s, dir=%s, console=%s",
        level, json_format, log_path, console)


def set_log_level(level: str) -> None:
    """动态调整根日志级别。"""
    level = level.upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ValueError(f"无效的日志级别: {level}")
    logging.getLogger().setLevel(level)
    logging.getLogger(__name__).info("日志级别已调整为: %s", level)


# 清理时的备份文件后缀模式 -> 保留天数（普通日志短、错误日志长）
_CLEANUP_PATTERNS = {
    "quantlab.log.*": "retention_days",
    "audit.jsonl.*": "retention_days",
    "error.log.*": "error_retention_days",
    "sync_worker_*.log.*": "retention_days",
}


def cleanup_old_logs(log_dir: str | Path = None,
                     retention_days: int = 7,
                     error_retention_days: int = 15) -> dict:
    """删除过期的日志轮转备份，保留足够长的错误日志用于定位问题。

    规则：
    - 只清理带轮转后缀的备份文件（quantlab.log.1、error.log.2、sync_worker_repair.log.1 等），
      当前正在写入的文件（无后缀）永不删除
    - 普通日志（quantlab.log/audit.jsonl/sync_worker_*.log 备份）保留 retention_days 天
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
