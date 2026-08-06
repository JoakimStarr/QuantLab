"""因子库 API：CRUD、评价、内置因子种子。"""
from fastapi import APIRouter, Query, BackgroundTasks

from app.core.errors import AppError
from app.schemas.common import ApiResponse
from app.schemas.factor import FactorCreate
from app.services.factor.library import (
    list_factors, get_factor, add_factor, disable_factor,
)
from app.services.factor.expression import validate_expression, ExpressionValidationError
from app.services.factor.builtin_factors import seed_builtin_factors

router = APIRouter(prefix="/factors", tags=["factor"])


@router.get("")
async def list_factors_api(
    category: str = Query(None),
    status: str = Query("active"),
    sort_by: str = Query("ic"),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
):
    items, total = await list_factors(category=category, status=status, sort_by=sort_by,
                                      limit=limit, offset=offset)
    return ApiResponse(ok=True, data={"items": items, "total": total})


@router.get("/{factor_id}")
async def get_factor_api(factor_id: int):
    item = await get_factor(factor_id)
    if item is None:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "因子不存在", "status": 404})
    return ApiResponse(ok=True, data=item)


@router.post("")
async def add_factor_api(body: FactorCreate):
    """新增因子（表达式经 AST 沙箱安全校验）。"""
    try:
        validate_expression(body.expression)
    except ExpressionValidationError as e:
        raise AppError("EXPR_INVALID", str(e), 422)
    item = await add_factor(
        name=body.name, expression=body.expression, category=body.category,
        description=body.description or None,
    )
    return ApiResponse(ok=True, data=item)


@router.delete("/{factor_id}")
async def disable_factor_api(factor_id: int):
    ok = await disable_factor(factor_id)
    if not ok:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "因子不存在", "status": 404})
    return ApiResponse(ok=True, data={"id": factor_id, "status": "disabled"})


@router.post("/seed-builtin")
async def seed_builtin_api():
    result = await seed_builtin_factors()
    return ApiResponse(ok=True, data=result)


async def _eval_factor_task(factor_id: int, start: str, end: str, universe: str = None):
    """后台因子评价任务（CPU 密集，应由 worker 执行）。"""
    try:
        from app.services.quant.qlib_init import QlibNotAvailableError
        from app.services.factor.library import evaluate_factor_by_id
        await evaluate_factor_by_id(factor_id, start, end, universe=universe)
    except QlibNotAvailableError as e:
        import logging
        logging.getLogger(__name__).error("qlib 不可用: %s", e)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("因子评价失败 factor_id=%s", factor_id)


@router.post("/{factor_id}/evaluate")
async def evaluate_factor_api(
    factor_id: int,
    background_tasks: BackgroundTasks,
    start_date: str = Query(None),
    end_date: str = Query(None),
    universe: str = Query(None, description="标的池 csi300/csi500/all/etf_all"),
):
    """触发因子评价（后台执行，结果写回因子库）。"""
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装，无法评价因子", 503)
    factor = await get_factor(factor_id)
    if factor is None:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "因子不存在", "status": 404})
    background_tasks.add_task(_eval_factor_task, factor_id, start_date, end_date, universe)
    return ApiResponse(ok=True, data={
        "message": f"因子 {factor_id} 评价已提交（后台执行）",
        "factor_id": factor_id,
    })
