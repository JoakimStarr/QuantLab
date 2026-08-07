"""结构化审计日志：记录关键操作（登录/登出/挖掘/回测提交）。

审计事件通过统一的 structlog 管道写入主日志（logs/quantlab.log），
logger 名为 "audit"，携带 action/user/resource/detail 等结构化字段。
前端日志页可按 logger=audit 过滤查看；不单独建文件（个人/自托管项目
无独立审计留档要求，减少日志文件数量与格式分叉）。
"""
import logging

logger = logging.getLogger("audit")


def audit(
    action: str,
    user: str = "anonymous",
    resource: str = "",
    detail: str = "",
    **extra,
) -> None:
    """记录一条审计日志。

    Args:
        action: 操作类型（login, logout, mining_submit, backtest_submit 等）
        user: 操作者（admin / 登录用户名 / token subject）
        resource: 操作对象（策略 ID、任务 ID 等）
        detail: 人类可读的描述（同时作为日志 message）
        **extra: 额外结构化字段
    """
    fields = {
        "action": action,
        "user": user,
        "resource": resource,
        "detail": detail,
        **extra,
    }
    # extra_fields 由 logging_config._extra_fields_processor 展平进 JSON 行；
    # 消息取 detail（更可读），前端 _entry_from_json 会合并 detail/action
    logger.info(detail or action, extra={"extra_fields": fields})
