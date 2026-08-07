"""策略库 API：规则/信号型策略模板列表 + 运行回测 + 回测历史（自动保存）。

前缀 /strategy-library，与 /strategies/{id} 无路径冲突。
回测是阻塞计算，经 run_in_executor 放入线程池执行，不阻塞事件循环。
每次运行回测自动落库一条历史（配置 + 结果快照），前端页面下方列表回看/重跑/删除。
"""
import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter, Query
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
    initial_capital: float = Field(10_000_000, ge=0, description="初始资金（元），默认 1000 万")


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
            req.initial_capital,
        )
    except ValueError as e:
        raise AppError("BACKTEST_FAILED", str(e), 422) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("策略库回测异常")
        raise AppError("BACKTEST_FAILED", f"回测失败: {e}", 500) from e

    # 自动保存历史（配置 + 结果快照）；保存失败不阻断回测，只记日志
    from app.services.strategy_rule_history import save_history
    history_id = await save_history(result)
    if history_id is not None:
        result["history_id"] = history_id

    return ApiResponse(ok=True, data=result)


@router.get("/history")
async def list_history_api(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    """策略库回测历史摘要列表（按时间倒序，不含净值/成交大字段）。"""
    from app.services.strategy_rule_history import list_history

    items, total = await list_history(limit=limit, offset=offset)
    return ApiResponse(ok=True, data={"items": items, "total": total})


@router.get("/history/{history_id}")
async def get_history_api(history_id: int):
    """单条回测历史完整详情（含参数/指标/净值曲线/成交明细）。"""
    from app.services.strategy_rule_history import get_history

    item = await get_history(history_id)
    if item is None:
        raise AppError("NOT_FOUND", "回测历史不存在", 404)
    return ApiResponse(ok=True, data=item)


@router.delete("/history/{history_id}")
async def delete_history_api(history_id: int):
    """软删除回测历史记录。"""
    from app.services.strategy_rule_history import delete_history

    ok = await delete_history(history_id)
    if not ok:
        raise AppError("NOT_FOUND", "回测历史不存在", 404)
    return ApiResponse(ok=True, data={"history_id": history_id, "message": "回测历史已删除"})
