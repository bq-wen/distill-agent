"""Private retrieval records and typed matches produced by the knowledge layer."""

import math

from pydantic import BaseModel, Field, model_validator

from personal_agent.contracts import PublicCitation, SourceMetadata


class KnowledgeChunk(BaseModel):
    """One private Markdown excerpt paired with a local embedding."""

    chunk_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    ordinal: int = Field(ge=0)
    heading: str | None = None
    content: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    embedding: list[float] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_embedding(self) -> "KnowledgeChunk":
        if not all(math.isfinite(value) for value in self.embedding):
            raise ValueError("知识向量必须全部是有限浮点数")
        if not any(value != 0 for value in self.embedding):
            raise ValueError("知识向量不能是零向量")
        return self


class RetrievalMatch(BaseModel):
    """Private evidence for the Agent; citation stays restricted to public metadata."""

    chunk: KnowledgeChunk
    source: SourceMetadata
    score: float
    rank: int = Field(ge=1)

    @property
    def public_citation(self) -> PublicCitation:
        return PublicCitation.from_source(self.source)
