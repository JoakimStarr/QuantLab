"""挖掘扩展 API：模板管理"""
import logging
from fastapi import APIRouter, Query, BackgroundTasks

from app.core.errors import AppError
from app.schemas.common import ApiResponse
from app.services.mining.mining_templates import list_templates, get_template

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mining", tags=["mining-ext"])


@router.get("/templates")
async def list_templates_api():
    """列出所有挖掘模板（添加11: 挖掘任务模板）"""
    return ApiResponse(ok=True, data={"items": list_templates()})


@router.get("/templates/{template_key}")
async def get_template_api(template_key: str):
    """获取模板详情"""
    tpl = get_template(template_key)
    if tpl is None:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "模板不存在", "status": 404})
    return ApiResponse(ok=True, data=tpl)


@router.post("/templates/{template_key}/run")
async def run_template_api(
    template_key: str,
    background_tasks: BackgroundTasks,
    n_candidates: int = Query(None),
):
    """使用模板启动 LLM 挖掘（添加11: 挖掘任务模板）"""
    from app.services.quant.qlib_init import is_qlib_available
    if not await is_qlib_available():
        raise AppError("QLIB_NOT_AVAILABLE", "qlib 未安装", 503)

    tpl = get_template(template_key)
    if tpl is None:
        return ApiResponse(ok=False, error={"code": "NOT_FOUND", "message": "模板不存在", "status": 404})

    from app.api.mining import _create_task, _run_llm_task
    from app.core.config import settings
    n = n_candidates or settings.mining.get("llm", {}).get("candidates_per_run", 5)
    params = {"n_candidates": n, "template": template_key, "prompt": tpl.get("llm_prompt", "")}
    task_id = await _create_task("llm", params)
    background_tasks.add_task(_run_llm_task, task_id, n)
    return ApiResponse(ok=True, data={
        "task_id": task_id,
        "template": template_key,
        "status": "pending",
        "message": f"模板 '{tpl['name']}' 挖掘已提交",
    })
