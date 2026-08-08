"""Indexing and hybrid retrieval service; no HTTP or WenGraph imports belong here."""

import hashlib
import math
from pathlib import Path

from personal_agent.knowledge.chunking import chunk_markdown
from personal_agent.knowledge.documents import (
    KnowledgeDocument,
    parse_markdown_document,
    parse_markdown_text,
)
from personal_agent.knowledge.embedding import EmbeddingProvider
from personal_agent.knowledge.models import KnowledgeChunk, RetrievalMatch
from personal_agent.knowledge.store import KnowledgeStore


class PersonalKnowledgeService:
    """Owns Markdown indexing plus semantic and exact-keyword retrieval."""

    def __init__(self, store: KnowledgeStore, embedding_provider: EmbeddingProvider) -> None:
        self.store = store
        self.embedding_provider = embedding_provider

    def index_directory(self, directory: str | Path) -> int:
        root = Path(directory)
        documents = [parse_markdown_document(path) for path in sorted(root.rglob("*.md"))]
        for document in documents:
            self.index_document(document)
        return len(documents)

    def index_document(self, document: KnowledgeDocument) -> int:
        chunks = chunk_markdown(document.content)
        vectors = self.embedding_provider.embed_many([content for _, content in chunks])
        records = [
            KnowledgeChunk(
                chunk_id=_chunk_id(document.metadata.source_id, ordinal, content),
                source_id=document.metadata.source_id,
                ordinal=ordinal,
                heading=heading,
                content=content,
                content_hash=_content_hash(content),
                embedding=vector,
            )
            for ordinal, ((heading, content), vector) in enumerate(
                zip(chunks, vectors, strict=True)
            )
        ]
        self.store.replace_source(document.metadata, records)
        return len(records)

    def index_markdown_text(self, raw: str, *, path: str) -> int:
        """Index a Markdown document that only exists in memory (distillation output)."""

        document = parse_markdown_text(raw, path=path)
        return self.index_document(document)

    def search_keywords(self, query: str, *, limit: int = 5) -> list[RetrievalMatch]:
        return self.store.search_keywords(query, limit=_validated_limit(limit))

    def search_hybrid(
        self,
        query: str,
        *,
        limit: int = 5,
        minimum_semantic_score: float = 0.0,
    ) -> list[RetrievalMatch]:
        """Production retrieval path: semantic (thresholded) merged with keyword hits.

        The same merge is used by the serving layer and the evaluation module, so
        eval numbers reflect exactly what users see. Fusion is de-duplication by
        chunk (semantic hit wins over the keyword copy), not score-level RRF: the
        corpus is small and the keyword side is only there to lock proper nouns.
        """

        semantic = [
            match
            for match in self.search_semantic(query, limit=limit)
            if match.score >= minimum_semantic_score
        ]
        keywords = self.search_keywords(query, limit=limit)
        by_chunk = {match.chunk.chunk_id: match for match in [*keywords, *semantic]}
        return list(by_chunk.values())[: _validated_limit(limit)]

    def search_semantic(self, query: str, *, limit: int = 5) -> list[RetrievalMatch]:
        candidates = self.store.semantic_candidates()
        if not candidates:
            return []
        query_vector = self.embedding_provider.embed(query)
        dimensions = len(query_vector)
        if any(len(chunk.embedding) != dimensions for chunk, _ in candidates):
            raise RuntimeError("索引向量维度与当前 Embedding 模型不一致，请重新建立索引")
        ranked = sorted(
            (
                (chunk, source, _cosine_similarity(query_vector, chunk.embedding))
                for chunk, source in candidates
            ),
            key=lambda item: (-item[2], item[0].source_id, item[0].ordinal),
        )[: _validated_limit(limit)]
        return [
            RetrievalMatch(chunk=chunk, source=source, score=score, rank=index)
            for index, (chunk, source, score) in enumerate(ranked, start=1)
        ]


def _validated_limit(limit: int) -> int:
    if not 1 <= limit <= 20:
        raise ValueError("检索 limit 必须在 1 到 20 之间")
    return limit


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _chunk_id(source_id: str, ordinal: int, content: str) -> str:
    identity = f"{source_id}\0{ordinal}\0{_content_hash(content)}"
    return f"chunk-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        raise ValueError("不能计算零向量的余弦相似度")
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)
