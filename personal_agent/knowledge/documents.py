"""Markdown source document parsing with explicit public citation metadata."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from personal_agent.contracts import PublicCitation, SourceMetadata


class KnowledgeDocumentFrontMatter(SourceMetadata):
    """Required metadata at the top of each version-controlled Markdown source."""

    version: int = Field(default=1, ge=1)


@dataclass(frozen=True)
class KnowledgeDocument:
    """Validated source content kept private to the application layer."""

    metadata: KnowledgeDocumentFrontMatter
    content: str
    path: Path

    @property
    def public_citation(self) -> PublicCitation:
        return PublicCitation.from_source(self.metadata)


class KnowledgeDocumentError(ValueError):
    """Raised when a Markdown document cannot satisfy the source contract."""


def parse_markdown_document(path: str | Path) -> KnowledgeDocument:
    """Parse one Markdown document with YAML front matter and non-empty content."""

    source_path = Path(path)
    raw = source_path.read_text(encoding="utf-8")
    if not raw.startswith("---\n"):
        raise KnowledgeDocumentError(f"知识资料缺少 YAML front matter: {source_path}")

    try:
        _, raw_metadata, content = raw.split("---\n", 2)
    except ValueError as error:
        raise KnowledgeDocumentError(f"知识资料 front matter 未闭合: {source_path}") from error

    try:
        metadata_data: Any = yaml.safe_load(raw_metadata)
    except yaml.YAMLError as error:
        raise KnowledgeDocumentError(f"知识资料元数据不是合法 YAML: {source_path}") from error
    if not isinstance(metadata_data, dict):
        raise KnowledgeDocumentError(f"知识资料元数据必须是对象: {source_path}")
    if not content.strip():
        raise KnowledgeDocumentError(f"知识资料正文不能为空: {source_path}")

    try:
        metadata = KnowledgeDocumentFrontMatter.model_validate(metadata_data)
    except ValidationError as error:
        raise KnowledgeDocumentError(f"知识资料元数据无效: {source_path}") from error
    return KnowledgeDocument(metadata=metadata, content=content.strip(), path=source_path)
