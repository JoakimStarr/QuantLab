"""技术文档 API：列表 + 详情 + 参考书库文件。"""
import urllib.parse
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.schemas.common import ApiResponse
from app.services.docs.loader import (
    list_docs,
    get_doc,
)

router = APIRouter(prefix="/docs", tags=["docs"])

# docs/book/qunat_book/（git 克隆的量化书库，.gitignore 已排除）
_BOOK_ROOT = (Path(__file__).resolve().parents[3] / "docs" / "book" / "qunat_book").resolve()


@router.get("")
async def list_docs_api():
    """列出所有技术文档（按 order 排序）。

    Returns:
        {ok: True, data: {docs: [{slug, title, order, group, summary, file}]}}
    """
    return ApiResponse(ok=True, data={"docs": list_docs()})


@router.get("/book/{filename}")
async def get_book_file_api(filename: str):
    """提供 docs/book/qunat_book/ 下的量化书籍 PDF/DOC（浏览器内联打开）。

    BOOKS.md 文档页中的链接指向此接口；对文件名做路径穿越防护。
    """
    name = urllib.parse.unquote(filename)
    target = (_BOOK_ROOT / name).resolve()
    if not target.is_relative_to(_BOOK_ROOT) or not target.is_file():
        raise HTTPException(status_code=404, detail="书籍文件不存在")
    return FileResponse(target, filename=name)


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
