import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
log_dir = PROJECT_ROOT / "logs"
log_dir.mkdir(parents=True, exist_ok=True)


class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging():
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    app_handler = RotatingFileHandler(log_dir / "app.log", maxBytes=10485760, backupCount=30, encoding="utf-8")
    app_handler.setLevel(logging.INFO)
    app_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

    error_handler = RotatingFileHandler(log_dir / "error.log", maxBytes=10485760, backupCount=30, encoding="utf-8")
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

    api_handler = RotatingFileHandler(log_dir / "api.jsonl", maxBytes=10485760, backupCount=30, encoding="utf-8")
    api_handler.setLevel(logging.INFO)
    api_handler.setFormatter(JsonFormatter())

    audit_handler = RotatingFileHandler(log_dir / "audit.jsonl", maxBytes=10485760, backupCount=30, encoding="utf-8")
    audit_handler.setLevel(logging.INFO)
    audit_handler.setFormatter(JsonFormatter())

    perf_handler = RotatingFileHandler(log_dir / "perf.jsonl", maxBytes=10485760, backupCount=30, encoding="utf-8")
    perf_handler.setLevel(logging.INFO)
    perf_handler.setFormatter(JsonFormatter())

    root_logger.addHandler(app_handler)
    root_logger.addHandler(error_handler)
    root_logger.addHandler(api_handler)
    root_logger.addHandler(audit_handler)
    root_logger.addHandler(perf_handler)

    return root_logger