"""docs loader 单测：标题回退链（frontmatter title > 首个 H1 > 文件名）与 wiki 路径式 slug。"""
import pytest

from app.services.docs import loader


@pytest.fixture
def docs_dir(tmp_path, monkeypatch):
    """构造临时 docs/ 目录（含 quant-wiki/docs/start/ 与顶层自有文档）并替换 DOCS_DIR。"""
    root = tmp_path
    (root / "README.md").write_text(
        "# 项目简介\n\n正文...\n", encoding="utf-8"
    )
    (root / "NO_TITLE.md").write_text(
        "# 中文一级标题\n\n这是正文\n", encoding="utf-8"
    )
    (root / "WITH_FM.md").write_text(
        "---\ntitle: 前端声明的标题\n---\n\n# 正文里的 H1\n", encoding="utf-8"
    )
    start = root / "quant-wiki" / "docs" / "start"
    start.mkdir(parents=True)
    (start / "event-driven.md").write_text(
        "\n# 事件驱动对冲基金入门：一文读懂来自突发事件的阿尔法\n\n正文\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "DOCS_DIR", root)
    return root


def test_parse_meta_fallback_to_h1(docs_dir):
    meta = loader._parse_meta(docs_dir / "NO_TITLE.md")
    assert meta["title"] == "中文一级标题"
    assert meta["slug"] == "no-title"


def test_parse_meta_frontmatter_wins_over_h1(docs_dir):
    meta = loader._parse_meta(docs_dir / "WITH_FM.md")
    assert meta["title"] == "前端声明的标题"


def test_parse_meta_builtin_wins(docs_dir):
    meta = loader._parse_meta(docs_dir / "README.md")
    assert meta["title"] == "项目简介"


def test_wiki_meta_title_from_h1(docs_dir):
    meta = loader._parse_wiki_meta(docs_dir / "quant-wiki" / "docs" / "start" / "event-driven.md", "start")
    assert meta["title"] == "事件驱动对冲基金入门：一文读懂来自突发事件的阿尔法"
    assert meta["group"] == "百科·入门"
    assert meta["slug"] == "quant-wiki__start__event-driven"


def test_list_docs_uses_chinese_titles(docs_dir):
    docs = loader.list_docs()
    by_slug = {d["slug"]: d for d in docs}
    assert by_slug["quant-wiki__start__event-driven"]["title"] == \
        "事件驱动对冲基金入门：一文读懂来自突发事件的阿尔法"
    assert by_slug["no-title"]["title"] == "中文一级标题"


def test_get_doc_wiki_title(docs_dir):
    doc = loader.get_doc("quant-wiki__start__event-driven")
    assert doc is not None
    assert doc["title"] == "事件驱动对冲基金入门：一文读懂来自突发事件的阿尔法"
    assert "正文" in doc["content"]
