from pathlib import Path

from personal_agent.knowledge.documents import parse_markdown_document
from personal_agent.knowledge.embedding import HashEmbeddingProvider
from personal_agent.knowledge.retrieval import PersonalKnowledgeService
from personal_agent.knowledge.store import KnowledgeStore


def _write_document(path: Path, *, content: str) -> None:
    path.write_text(
        f"""---
source_id: wengraph-overview
project: WenGraph
title: WenGraph 架构说明
visibility: private
public_summary: 自研 Agent 图运行时。
public_url: https://github.com/bq-wen/wengraph
---
{content}
""",
        encoding="utf-8",
    )


def test_indexes_markdown_and_returns_keyword_and_semantic_evidence(tmp_path: Path) -> None:
    document_path = tmp_path / "wengraph.md"
    _write_document(
        document_path,
        content="""# WenGraph

WenGraph 是自研 Agent 图运行时，提供 StatePatch 和 ToolGuard。

# 安全

ToolGuard 结合 CapabilityPolicy 和 RiskPolicy 审核工具调用。""",
    )
    store = KnowledgeStore(tmp_path / "knowledge.db")
    service = PersonalKnowledgeService(store, HashEmbeddingProvider())
    try:
        assert service.index_document(parse_markdown_document(document_path)) == 2

        keyword_matches = service.search_keywords("ToolGuard")
        semantic_matches = service.search_semantic("Agent 图运行时")

        assert keyword_matches[0].chunk.heading == "安全"
        assert semantic_matches[0].chunk.heading == "WenGraph"
        assert keyword_matches[0].public_citation.model_dump(mode="json") == {
            "source_id": "wengraph-overview",
            "project": "WenGraph",
            "title": "WenGraph 架构说明",
            "summary": "自研 Agent 图运行时。",
            "url": "https://github.com/bq-wen/wengraph",
        }
    finally:
        store.close()


def test_reindex_replaces_old_chunks_and_fts_entries(tmp_path: Path) -> None:
    document_path = tmp_path / "wengraph.md"
    store = KnowledgeStore(tmp_path / "knowledge.db")
    service = PersonalKnowledgeService(store, HashEmbeddingProvider())
    try:
        _write_document(document_path, content="# 初版\n\nobsolete_token")
        service.index_document(parse_markdown_document(document_path))

        _write_document(document_path, content="# 新版\n\ncurrent_token")
        service.index_document(parse_markdown_document(document_path))

        assert store.count_chunks() == 1
        assert service.search_keywords("obsolete_token") == []
        matches = service.search_keywords("current_token")
        assert len(matches) == 1
        assert matches[0].chunk.content.endswith("current_token")
    finally:
        store.close()
