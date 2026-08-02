"""技术文档 API：列表 + 详情。"""
from fastapi import APIRouter, HTTPException

from app.schemas.common import ApiResponse
from app.services.docs.loader import (
    list_docs,
    get_doc,
)

router = APIRouter(prefix="/docs", tags=["docs"])


@router.get("")
async def list_docs_api():
    """列出所有技术文档（按 order 排序）。

    Returns:
        {ok: True, data: {docs: [{slug, title, order, group, summary, file}]}}
    """
    return ApiResponse(ok=True, data={"docs": list_docs()})


@router.get("/{slug}")
async def get_doc_api(slug: str):
    """按 slug 获取文档详情（含 frontmatter、meta）。

    Returns:
        {ok: True, data: {slug, title, order, group, summary, content, file}}
    """
    doc = get_doc(slug)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在: " + slug)
    return ApiResponse(ok=True, data=doc)