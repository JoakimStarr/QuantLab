"""技术文档 API：列表和详情。"""
from fastapi import APIRouter, HTTPException
from app.services.docs.loader import list_docs, get_doc

router = APIRouter(prefix="/docs", tags=["docs"])


@router.get("")
async def list_docs_api():
    """列出所有技术文档（按 order 排序）。

    Returns:
        list of {slug, title, order, group, summary, file}
    """
    return {"docs": list_docs()}


@router.get("/{slug}")
async def get_doc_api(slug: str):
    """按 slug 获取文档内容（Markdown原文）。

    Args:
        slug: 文档标识，如 data-layer

    Returns:
        {slug, title, order, group, summary, content, file}

    Raises:
        404: 文档不存在
    """
    doc = get_doc(slug)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在: " + slug)
    return doc

