import json
import logging
import os
import sys
import contextvars
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import settings

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT") or Path(__file__).resolve().parent.parent.parent.parent)
log_dir = PROJECT_ROOT / "logs"
log_dir.mkdir(parents=True, exist_ok=True)

# request_id 上下文变量，由中间件设置，由 RequestIdFilter 注入到每条日志记录
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")


class RequestIdFilter(logging.Filter):
    """将 contextvars 中的 request_id 注入到每条 LogRecord 上。"""
    def filter(self, record):
        record.request_id = request_id_var.get("")
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", ""),
        }
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """文本格式，包含 request_id。"""
    def format(self, record):
        rid = getattr(record, "request_id", "")
        base = super().format(record)
        if rid:
            return f"{base} [req={rid}]"
        return base


def setup_logging():
    cfg = settings.logging or {}
    handlers_cfg = cfg.get("handlers", {})
    level_str = cfg.get("level", "INFO")
    level = getattr(logging, level_str.upper(), logging.INFO)
    max_bytes = handlers_cfg.get("app", {}).get("max_bytes", 10485760)
    backup_count = handlers_cfg.get("app", {}).get("backup_count", 30)

    rid_filter = RequestIdFilter()

    # --- 文本 handler（app.log + error.log）---
    text_fmt = TextFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    app_handler = RotatingFileHandler(
        log_dir / "app.log", maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
    app_handler.setLevel(level)
    app_handler.setFormatter(text_fmt)
    app_handler.addFilter(rid_filter)

    error_handler = RotatingFileHandler(
        log_dir / "error.log", maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(text_fmt)
    error_handler.addFilter(rid_filter)

    # --- JSON handler（api.jsonl + perf.jsonl）---
    json_fmt = JsonFormatter()

    api_handler = RotatingFileHandler(
        log_dir / "api.jsonl", maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
    api_handler.setLevel(level)
    api_handler.setFormatter(json_fmt)
    api_handler.addFilter(rid_filter)

    perf_handler = RotatingFileHandler(
        log_dir / "perf.jsonl", maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
    perf_handler.setLevel(level)
    perf_handler.setFormatter(json_fmt)
    perf_handler.addFilter(rid_filter)

    # --- root logger: 仅 app + error ---
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(app_handler)
    root_logger.addHandler(error_handler)

    # --- 专用 logger: 不向 root 传播，避免重复 ---
    api_logger = logging.getLogger("app.api")
    api_logger.setLevel(level)
    api_logger.addHandler(api_handler)
    api_logger.propagate = False

    perf_logger = logging.getLogger("perf")
    perf_logger.setLevel(level)
    perf_logger.addHandler(perf_handler)
    perf_logger.propagate = False

    # --- stdout handler（开发模式）---
    if settings.app.get("debug", False):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(text_fmt)
        console_handler.addFilter(rid_filter)
        root_logger.addHandler(console_handler)

    return root_logger
