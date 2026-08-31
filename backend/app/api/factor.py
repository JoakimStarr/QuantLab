"""因子库 API：CRUD、评价、内置因子种子。"""
from fastapi import APIRouter, Query

from app.core.errors import AppError
from app.schemas.common import ApiResponse
from app.schemas.factor import FactorCreate
from app.services.factor.library import (
    list_factors, get_factor, get_factor_summary, add_factor, disable_factor,
)
from app.services.factor.expression import validate_expression, ExpressionValidationError
from app.services.factor.builtin_factors import seed_builtin_factors

router = APIRouter(prefix="/factors", tags=["factor"])


@router.get("")
async def list_factors_api(
    category: str = Query(None),
    status: str = Query("active"),
    sort_by: str = Query("ic"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    keyword: str = Query(None, description="名称/表达式/描述模糊搜索"),
    ids: str = Query(None, description="逗号分隔的因子 ID 白名单（如仅衰减视图）"),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
):
    id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()] if ids else None
    items, total = await list_factors(category=category, status=status, sort_by=sort_by,
                                      sort_order=sort_order, keyword=keyword, ids=id_list,
                                      limit=limit, offset=offset)
    return ApiResponse(ok=True, data={"items": items, "total": total})


@router.get("/summary")
async def factor_summary_api():
    """因子库概览统计（总数/已评价/平均 IC/类别分布），列表页按需加载。"""
    return ApiResponse(ok=True, data=await get_factor_summary())


@router.get("/expression-schema")
async def expression_schema_api():
    """表达式白名单（算子+字段），供前端编辑器自动补全（须在 /{factor_id} 前注册）。"""
    from app.services.factor.expression import get_expression_schema
    return ApiResponse(ok=True, data=get_expression_schema())


@router.get("/{factor_id}/eval-status")
async def factor_eval_status_api(factor_id: int):
    """查询因子评价状态：是否正在评价（独立子进程）、最近评价时间。

    须在 /{factor_id} 前注册，避免被路径参数路由遮蔽。
    """
    from app.services.factor.eval_worker import is_factor_eval_running
    factor = await get_factor(factor_id)
    if factor is None:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "因子不存在", "status": 404})
    return ApiResponse(ok=True, data={
        "factor_id": factor_id,
        "running": is_factor_eval_running(factor_id),
        "evaluated_at": factor.get("evaluated_at"),
    })


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


@router.post("/{factor_id}/evaluate")
async def evaluate_factor_api(
    factor_id: int,
    start_date: str = Query(None),
    end_date: str = Query(None),
    universe: str = Query(None, description="标的池 csi300/csi500/all/etf_all"),
):
    """触发因子评价（独立 worker 子进程执行，不占 web 进程）。

    评价跑在独立子进程（eval_worker）里，避免：
    - 阻塞 web 事件循环 / 占用 web 进程的 ProcessPoolExecutor
    - uvicorn --reload 关停时 join 进程池导致服务卡死
    """
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装，无法评价因子", 503)
    factor = await get_factor(factor_id)
    if factor is None:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "因子不存在", "status": 404})
    from app.services.factor.eval_worker import is_factor_eval_running, spawn_factor_eval_worker
    if is_factor_eval_running(factor_id):
        return ApiResponse(ok=True, data={
            "message": f"因子 {factor_id} 正在评价中，请勿重复提交",
            "factor_id": factor_id, "running": True,
        })
    spawn_factor_eval_worker(factor_id, start_date, end_date, universe)
    return ApiResponse(ok=True, data={
        "message": f"因子 {factor_id} 评价已提交（独立进程执行）",
        "factor_id": factor_id, "running": True,
    })
