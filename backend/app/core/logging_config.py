"""日志系统结构化配置：JSON 格式 + 日志轮转 + 动态级别调整。

设计：
- 统一 JSON 结构化日志格式：{"timestamp", "level", "logger", "message", "request_id", "module", "duration_ms"}
- 日志轮转：RotatingFileHandler（最大 100MB，保留 5 份）
- 日志级别动态调整：通过 API 端点 /api/v1/admin/log-level 实时调整
"""
import contextvars
import json
import logging
import logging.config
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path


# 请求级上下文变量，由中间件设置，由日志过滤器/错误处理器读取
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
# 日志目录，由 logs API 路由引用
log_dir: Path = Path("logs")


class JSONFormatter(logging.Formatter):
    """JSON 结构化日志格式化器。"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        # 注入 request_id（如果 LoggerAdapter 有）
        if hasattr(record, "request_id"):
            log_entry["request_id"] = record.request_id
        if hasattr(record, "task_id"):
            log_entry["task_id"] = record.task_id
        if hasattr(record, "duration_ms"):
            log_entry["duration_ms"] = record.duration_ms
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging(log_dir: str = "logs", level: str = "INFO", json_format: bool = True) -> None:
    """配置根日志器。

    Args:
        log_dir: 日志目录（相对于项目根或绝对路径）
        level: 日志级别
        json_format: True 使用 JSON 格式，False 使用标准文本格式
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    # 更新模块级变量，供 logs API 路由使用
    globals()["log_dir"] = log_path

    handlers = {
        "console": {
            "class": "logging.StreamHandler",
            "stream": sys.stdout,
            "formatter": "json" if json_format else "standard",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(log_path / "quantlab.log"),
            "maxBytes": 100 * 1024 * 1024,  # 100MB
            "backupCount": 5,
            "encoding": "utf-8",
            "formatter": "json" if json_format else "standard",
        },
        "error_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(log_path / "error.log"),
            "maxBytes": 100 * 1024 * 1024,  # 100MB
            "backupCount": 5,
            "encoding": "utf-8",
            "level": "WARNING",
            "formatter": "json" if json_format else "standard",
        },
    }

    formatters = {
        "json": {
            "()": "app.core.logging_config.JSONFormatter",
        },
        "standard": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
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