"""策略库 API：规则/信号型策略模板列表 + 运行回测（v1 不持久化，运行即返回）。

前缀 /strategy-library，与 /strategies/{id} 无路径冲突。
回测是阻塞计算，经 run_in_executor 放入线程池执行，不阻塞事件循环。
"""
import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.errors import AppError
from app.schemas.common import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/strategy-library", tags=["strategy-library"])


class RuleBacktestRequest(BaseModel):
    template: str
    params: dict = Field(default_factory=dict)
    symbols: list[str] = Field(min_length=1)
    start: str
    end: str
    benchmark: str = "SH000300"


@router.get("/templates")
async def list_templates_api():
    """策略模板列表（含参数 schema，前端据此渲染动态表单）。"""
    from app.services.quant.rule_backtest import list_templates

    items = list_templates()
    return ApiResponse(ok=True, data={"items": items, "total": len(items)})


@router.post("/backtest")
async def run_backtest_api(req: RuleBacktestRequest):
    """运行规则策略回测，返回指标/净值曲线/交易记录。"""
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装或数据未同步", 503)

    from app.services.quant.rule_backtest import TEMPLATES, run_rule_backtest

    if req.template not in TEMPLATES:
        raise AppError("TEMPLATE_NOT_FOUND", f"未知策略模板: {req.template}", 404)
    for d in (req.start, req.end):
        try:
            datetime.strptime(d, "%Y-%m-%d")
        except ValueError as e:
            raise AppError("VALIDATION_ERROR", f"日期格式应为 YYYY-MM-DD: {d}", 422) from e
    if req.start > req.end:
        raise AppError("VALIDATION_ERROR", "开始日期不能晚于结束日期", 422)

    try:
        result = await asyncio.get_running_loop().run_in_executor(
            None, run_rule_backtest,
            req.template, req.params, req.symbols, req.start, req.end, req.benchmark,
        )
    except ValueError as e:
        raise AppError("BACKTEST_FAILED", str(e), 422) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("策略库回测异常")
        raise AppError("BACKTEST_FAILED", f"回测失败: {e}", 500) from e

    return ApiResponse(ok=True, data=result)
