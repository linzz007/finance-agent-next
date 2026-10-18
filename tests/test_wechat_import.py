from pathlib import Path

from policy_impact.domains.finance.wechat_import import import_sources, parse_html, parse_markdown


def test_parse_wechat_html_extracts_metadata_and_body():
    html = """
    <html><head><meta property="og:title" content="黄金观察" />
    <meta property="og:url" content="https://mp.weixin.qq.com/s/demo" /></head>
    <body><span id="publish_time">2026年10月01日</span>
    <div id="js_content"><p>黄金价格受到实际利率与美元因素影响。</p>
    <p>这是一段足够长的公开文章正文，用于验证导入器会保留文章内容并识别来源链接。</p></div></body></html>
    """
    result = parse_html(html, source="demo.html", author="TT")
    assert result.title == "黄金观察"
    assert result.published_at == "2026-10-01"
    assert result.source_url == "https://mp.weixin.qq.com/s/demo"
    assert "实际利率" in result.content


def test_import_markdown_writes_author_folder_and_deduplicates(tmp_path: Path):
    source = tmp_path / "exports"
    source.mkdir()
    article = source / "gold.md"
    article.write_text(
        "# 黄金观察\n\n发布日期：2026-10-01\n\n"
        "黄金可能受到实际利率变化影响。这是用于导入测试的正文。\n"
        "来源：[原文](https://mp.weixin.qq.com/s/demo)\n",
        encoding="utf-8",
    )
    root = tmp_path / "articles"
    first = import_sources(inputs=[article], root=root, author="TT", company_id="test", sync=False)
    second = import_sources(inputs=[article], root=root, author="TT", company_id="test", sync=False)
    assert first["summary"]["imported"] == 1
    assert second["summary"]["skipped"] == 1
    files = list((root / "TT" / "articles").glob("*.md"))
    assert len(files) == 1
    assert "2026-10-01" in files[0].name


def test_markdown_parser_uses_frontmatter_and_does_not_require_source_url():
    result = parse_markdown(
        "---\ntitle: Macro view\npublished_at: 2026-09-30\n---\n\n# Macro view\n\nA source text.",
        source="macro.md",
        author="培风客",
    )
    assert result.title == "Macro view"
    assert result.published_at == "2026-09-30"
    assert result.source_url == ""
