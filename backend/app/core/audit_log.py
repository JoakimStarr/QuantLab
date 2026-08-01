"""结构化审计日志：记录关键操作（登录/登出/挖掘/回测/导出等）。

写入 logs/audit.jsonl，每行一条 JSON，便于 ELK/Loki 采集与合规审计。
"""
import logging
from datetime import datetime

from pythonjsonlogger import jsonlogger

from app.core.config import settings

logger = logging.getLogger("audit")


def _ensure_audit_handler() -> None:
    """确保 audit logger 有 file handler（懒加载，避免 import 时创建文件）。"""
    if logger.handlers:
        return
    log_dir = settings.PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(
        str(log_dir / "audit.jsonl"), encoding="utf-8",
    )
    handler.setFormatter(
        jsonlogger.JsonFormatter(
            "%(asctime)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False  # 不传播到 root logger，避免重复输出


def audit(
    action: str,
    user: str = "anonymous",
    resource: str = "",
    detail: str = "",
    **extra,
) -> None:
    """记录一条审计日志。

    Args:
        action: 操作类型（login, logout, mining_submit, backtest_submit, export 等）
        user: 操作者（admin / token subject）
        resource: 操作对象（因子名、策略 ID 等）
        detail: 人类可读的描述
        **extra: 额外结构化字段
    """
    _ensure_audit_handler()
    entry = {
        "timestamp": datetime.now().isoformat(),
        "action": action,
        "user": user,
        "resource": resource,
        "detail": detail,
        **extra,
    }
    logger.info("audit", extra=entry)