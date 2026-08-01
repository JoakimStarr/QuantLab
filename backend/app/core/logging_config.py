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
import sys
from logging.handlers import RotatingFileHandler
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


def setup_logging(log_dir: str = "logs", level: str = "INFO", json_format: bool = True) -> None:
    """配置根日志器。

    Args:
        log_dir: 日志目录（相对于项目根或绝对路径）
        level: 日志级别
        json_format: True 使用 JSON 格式（structlog JSONRenderer），
                     False 使用 ConsoleRenderer（文本格式）
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    # 更新模块级变量，供 logs API 路由使用
    globals()["log_dir"] = log_path

    # 共享处理器链：structlog 日志器与标准库日志器共用
    pre_chain = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
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
        "console": {
            "class": "logging.StreamHandler",
            "stream": sys.stdout,
            "formatter": "structlog",
        },
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

    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": formatters,
        "handlers": handlers,
        "root": {
            "level": level.upper(),
            "handlers": ["console", "file", "error_file"],
        },
    }

    logging.config.dictConfig(config)
    logging.getLogger(__name__).info("日志系统已初始化: level=%s, json_format=%s, dir=%s", level, json_format, log_path)


def set_log_level(level: str) -> None:
    """动态调整根日志级别。"""
    level = level.upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ValueError(f"无效的日志级别: {level}")
    logging.getLogger().setLevel(level)
    logging.getLogger(__name__).info("日志级别已调整为: %s", level)