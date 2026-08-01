"""技术文档 API：列表 + 详情 + 原始 markdown（供 Docsify 渲染用）。"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from app.schemas.common import ApiResponse
from app.services.docs.loader import (
    list_docs,
    get_doc,
    get_doc_raw,
    get_sidebar_md,
    get_navbar_md,
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


# ---------------------------------------------------------------------------
# Docsify 集成端点（返回原始 Markdown，text/markdown 格式）
# ---------------------------------------------------------------------------

# 文档名 -> 文档真实路径的映射（Docsify 的 _sidebar.md 用 slug 作锚点）
def _readme_md() -> str:
    """README.md 来自仓库根目录，剥 frontmatter 后返回。

    路径推导：
      backend/app/api/docs.py → parents[0] = backend/app/api
                            parents[1] = backend/app
                            parents[2] = backend
                            parents[3] = 项目根（QuantLab/，README.md 在这里）
    """
    from pathlib import Path
    from app.services.docs.loader import _strip_frontmatter
    p = Path(__file__).resolve().parents[3] / "README.md"
    if not p.exists():
        return "# QuantLab\n\nREADME.md 缺失。"
    return _strip_frontmatter(p.read_text(encoding="utf-8"))


_DOC_ALIASES = {
    "_sidebar.md": lambda: get_sidebar_md(),
    "_navbar.md": lambda: get_navbar_md(),
    "README.md": _readme_md,
    "readme": _readme_md,
}


def _try_get_md(name: str) -> str | None:
    """按 Docsify 路径返回 markdown 原文。先查别名，再查 slug。"""
    alias = _DOC_ALIASES.get(name)
    if alias is not None:
        return alias()
    # name 可能是 "data-layer.md" 或 "data-layer"，都接受
    slug = name[:-3] if name.endswith(".md") else name
    return get_doc_raw(slug)


@router.get("/md/{name:path}", response_class=PlainTextResponse)
async def get_doc_md(name: str):
    """Docsify 集成端点：返回原始 markdown（text/plain）。

    Docsify 通过 fetch('/api/v1/docs/md/<slug>') 拿到 markdown 直接渲染。
    支持特殊名 `_sidebar.md` / `_navbar.md`（自动从元数据生成）。
    """
    content = _try_get_md(name)
    if content is None:
        raise HTTPException(status_code=404, detail="文档不存在: " + name)
    return PlainTextResponse(content, media_type="text/markdown; charset=utf-8")