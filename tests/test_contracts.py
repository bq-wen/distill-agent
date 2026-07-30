import pytest
from pydantic import ValidationError

from personal_agent.contracts import (
    KnowledgeVisibility,
    PublicCitation,
    SourceMetadata,
)


def test_public_source_requires_a_public_summary() -> None:
    with pytest.raises(ValidationError, match="public_summary"):
        SourceMetadata(
            source_id="wengraph-overview",
            project="WenGraph",
            title="概览",
            visibility=KnowledgeVisibility.PUBLIC,
        )


def test_citation_only_contains_approved_public_metadata() -> None:
    source = SourceMetadata(
        source_id="wengraph-overview",
        project="WenGraph",
        title="WenGraph 概览",
        visibility=KnowledgeVisibility.PRIVATE,
        public_summary="自研的 Agent 图运行时。",
        public_url="https://github.com/bq-wen/wengraph",
    )

    assert PublicCitation.from_source(source).model_dump(mode="json") == {
        "source_id": "wengraph-overview",
        "project": "WenGraph",
        "title": "WenGraph 概览",
        "summary": "自研的 Agent 图运行时。",
        "url": "https://github.com/bq-wen/wengraph",
    }
