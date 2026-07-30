from pathlib import Path

import pytest

from personal_agent.contracts import KnowledgeVisibility
from personal_agent.knowledge.documents import KnowledgeDocumentError, parse_markdown_document


def test_parse_markdown_document_with_private_source(tmp_path: Path) -> None:
    document_path = tmp_path / "wengraph.md"
    document_path.write_text(
        """---
source_id: wengraph-overview
project: WenGraph
title: WenGraph 概览
visibility: private
public_summary: 自研 Agent 图运行时。
public_url: https://github.com/bq-wen/wengraph
---
# 私有扩展资料

只供 Agent 检索的细节。
""",
        encoding="utf-8",
    )

    document = parse_markdown_document(document_path)

    assert document.metadata.visibility is KnowledgeVisibility.PRIVATE
    assert document.content == "# 私有扩展资料\n\n只供 Agent 检索的细节。"


def test_parse_markdown_document_rejects_missing_front_matter(tmp_path: Path) -> None:
    document_path = tmp_path / "invalid.md"
    document_path.write_text("没有元数据", encoding="utf-8")

    with pytest.raises(KnowledgeDocumentError, match="front matter"):
        parse_markdown_document(document_path)
