"""技术文档加载器：扫描 docs/*.md，返回结构化目录和内容。

支持的元数据 frontmatter（可选，用 python-frontmatter 解析）：
---
title: 文档标题
slug: doc-id
order: 1
group: 分组
summary: 一句话说明
---

无 frontmatter 时自动用文件名（去.md）作为 slug，第一行 # 标题作为 title。
"""
import re
from pathlib import Path
from typing import Optional

# backend/app/services/docs/loader.py -> backend/app/services/docs/ -> backend/app/services/
# -> backend/app/ -> backend/ -> 4 上一层到 backend, 再上一层到 QuantLab
# loader.py 位于 backend/app/services/docs/loader.py，docs/ 在 backend/ 的上一级
# __file__ = .../backend/app/services/docs/loader.py
# parents[0] = docs/  [1] = services/  [2] = app/  [3] = backend/  [4] = 项目根
DOCS_DIR = Path(__file__).resolve().parents[4] / "docs"  # 项目根下的 docs/

# ---- quant-wiki 集成 ----
# 用户克隆的 quant-wiki 仓库位于 docs/quant-wiki/，内容在 docs/quant-wiki/docs/<分类>/
# 只收录量化知识分类；job(求职)/repo(仓库导航)/顶层元页 不属于平台知识，不收录。
_WIKI_ROOT_REL = "quant-wiki/docs"
_WIKI_CATEGORIES: dict[str, str] = {
    "basic": "百科·基础",
    "ai": "百科·AI",
    "start": "百科·入门",
    "advanced": "百科·进阶",
    "industry": "百科·行业",
    "library": "百科·代码库",
}
_WIKI_ORDER = {cat: i for i, cat in enumerate(_WIKI_CATEGORIES)}

# 内置文档元数据（frontmatter 不可用时的回退）
_BUILTIN_META = {
    "DATA_LAYER.md": {
        "title": "数据层架构",
        "slug": "data-layer",
        "order": 1,
        "group": "架构",
        "summary": "数据层设计：涨跌停mask、基本面PIT、资金情绪采集",
    },
    "TECHNICAL.md": {
        "title": "技术选型",
        "slug": "technical",
        "order": 2,
        "group": "架构",
        "summary": "框架选型、数据流、关键设计决策",
    },
    "DEVELOPMENT.md": {
        "title": "开发手册",
        "slug": "development",
        "order": 3,
        "group": "开发",
        "summary": "开发环境、构建部署、代码规范",
    },
    "FACTOR_ENGINE.md": {
        "title": "因子引擎",
        "slug": "factor-engine",
        "order": 4,
        "group": "因子",
        "summary": "QLib表达式语法、因子评价指标、协同性评估",
    },
    "API_REFERENCE.md": {
        "title": "API参考",
        "slug": "api-reference",
        "order": 5,
        "group": "API",
        "summary": "后端API接口文档",
    },
    "README.md": {
        "title": "项目简介",
        "slug": "README",
        "order": 0,
        "group": "概览",
        "summary": "QuantLab 项目概述",
    },
}


def _strip_frontmatter(content: str) -> str:
    """剥掉 markdown 文件开头的 YAML frontmatter（如果存在）。

    用纯正则实现，不依赖 python-frontmatter，避免未安装时的 fallback bug。
    前置 frontmatter 形如：
        ---
        key: value
        ---
        正文...
    """
    # 行首以 "---" 开头，第二行必须是另一个 "---" 才算完整 frontmatter
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", content, re.DOTALL)
    if m:
        return m.group(2).lstrip("\n")
    return content


def _parse_frontmatter(content: str) -> dict:
    """从 markdown 字符串提取 YAML frontmatter 为 dict（无依赖实现）。"""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not m:
        return {}
    fm_text = m.group(1)
    out: dict = {}
    for line in fm_text.split("\n"):
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            out[k] = v
    return out


def _slugify(filename: str) -> str:
    """文件名转 slug: DATA_LAYER.md -> data-layer"""
    name = filename.rsplit(".", 1)[0]
    return re.sub(r"[_\s]+", "-", name).lower()


def _parse_meta(path: Path) -> dict:
    """解析 MD 文件的元数据。优先 frontmatter，回退到内置表。"""
    content = path.read_text(encoding="utf-8")

    # 内置元数据作为 base，frontmatter 解析后若提供再覆盖
    builtin = _BUILTIN_META.get(path.name, {})
    base = {
        "title": builtin.get("title") or path.stem,
        "slug": builtin.get("slug") or _slugify(path.name),
        "order": builtin.get("order", 999),
        "group": builtin.get("group", "未分类"),
        "summary": builtin.get("summary", ""),
    }

    # 解析 frontmatter（先纯正则；如有 python-frontmatter 包，再覆盖更精确的值）
    fm = _parse_frontmatter(content)
    for key in ("title", "slug", "group", "summary"):
        v = fm.get(key)
        if v:
            base[key] = v
    v = fm.get("order")
    if v is not None:
        try:
            base["order"] = int(v)
        except (TypeError, ValueError):
            pass

    body = _strip_frontmatter(content)
    return {**base, "content": body}


def _wiki_category(path: Path) -> str | None:
    """判断是否为已收录的 quant-wiki 文档，返回分类名；否则 None。"""
    try:
        rel = path.relative_to(DOCS_DIR)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) >= 3 and parts[0] == "quant-wiki" and parts[1] == "docs":
        if parts[2] in _WIKI_CATEGORIES:
            return parts[2]
    return None


def _parse_wiki_meta(path: Path, cat: str) -> dict:
    """解析 quant-wiki 文档元数据：路径式 slug（防 425 篇撞名）+ 中文分类 group。"""
    content = path.read_text(encoding="utf-8")
    fm = _parse_frontmatter(content)
    rel = path.relative_to(DOCS_DIR)
    inner = "__".join(rel.parts[3:])  # quant-wiki/docs/<cat>/... 之后的子路径
    slug = "quant-wiki__" + cat + ("__" + inner if inner else "")
    if slug.endswith(".md"):
        slug = slug[:-3]
    return {
        "title": fm.get("title") or path.stem,
        "slug": slug,
        "group": _WIKI_CATEGORIES[cat],
        "order": 100 + _WIKI_ORDER[cat],
        "summary": fm.get("summary", ""),
        "content": _strip_frontmatter(content),
    }


def _wiki_doc_dict(path: Path, cat: str) -> dict:
    meta = _parse_wiki_meta(path, cat)
    return {
        "slug": meta["slug"],
        "title": meta["title"],
        "order": meta["order"],
        "group": meta["group"],
        "summary": meta["summary"],
        "file": str(path.relative_to(DOCS_DIR)),
    }


def list_docs() -> list:
    """列出所有文档（含元数据），按 order 排序。

    Returns:
        list of {slug, title, order, group, summary, file}
    """
    if not DOCS_DIR.exists():
        return []
    docs = []
    # 1) 顶层 QuantLab 自有文档
    for path in sorted(DOCS_DIR.glob("*.md")):
        meta = _parse_meta(path)
        docs.append({
            "slug": meta["slug"],
            "title": meta["title"],
            "order": meta["order"],
            "group": meta["group"],
            "summary": meta["summary"],
            "file": path.name,
        })
    # 2) quant-wiki 文档（按分类递归，只收录 _WIKI_CATEGORIES）
    for cat in _WIKI_CATEGORIES:
        cat_root = DOCS_DIR / _WIKI_ROOT_REL / cat
        if not cat_root.exists():
            continue
        for path in sorted(cat_root.rglob("*.md")):
            docs.append(_wiki_doc_dict(path, cat))
    docs.sort(key=lambda d: (d["order"], d["group"], d["title"]))
    return docs


def get_doc(slug: str) -> Optional[dict]:
    """按 slug 获取文档内容。

    Returns:
        {slug, title, order, group, summary, content, file} 或 None
    """
    if not DOCS_DIR.exists():
        return None
    # quant-wiki 文档
    for cat in _WIKI_CATEGORIES:
        cat_root = DOCS_DIR / _WIKI_ROOT_REL / cat
        if not cat_root.exists():
            continue
        for path in cat_root.rglob("*.md"):
            meta = _parse_wiki_meta(path, cat)
            if meta["slug"] == slug:
                return {**meta, "file": str(path.relative_to(DOCS_DIR))}
    # QuantLab 自有文档
    for path in DOCS_DIR.glob("*.md"):
        meta = _parse_meta(path)
        if meta["slug"] == slug or _slugify(path.name) == slug:
            return {
                "slug": meta["slug"],
                "title": meta["title"],
                "order": meta["order"],
                "group": meta["group"],
                "summary": meta["summary"],
                "content": meta["content"],
                "file": path.name,
            }
    return None
