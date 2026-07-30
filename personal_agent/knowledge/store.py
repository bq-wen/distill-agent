"""SQLite persistence for private chunks and visitor-safe source metadata."""

import json
import sqlite3
from pathlib import Path

from personal_agent.contracts import KnowledgeVisibility, SourceMetadata
from personal_agent.knowledge.models import KnowledgeChunk, RetrievalMatch


class KnowledgeStore:
    """Small-corpus SQLite store; vector ranking remains exact O(N) by design."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        if self.path != Path(":memory:"):
            self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS knowledge_sources (
                source_id TEXT PRIMARY KEY, project TEXT NOT NULL, title TEXT NOT NULL,
                visibility TEXT NOT NULL, public_summary TEXT, public_url TEXT
            );
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                chunk_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES knowledge_sources(source_id)
                    ON DELETE CASCADE,
                ordinal INTEGER NOT NULL, heading TEXT, content TEXT NOT NULL,
                content_hash TEXT NOT NULL, embedding_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_source ON knowledge_chunks(source_id, ordinal);
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunk_fts USING fts5(
                chunk_id UNINDEXED, source_id UNINDEXED, content, tokenize='unicode61'
            );
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def replace_source(self, source: SourceMetadata, chunks: list[KnowledgeChunk]) -> None:
        if any(chunk.source_id != source.source_id for chunk in chunks):
            raise ValueError("chunk source_id 必须与来源一致")
        with self.connection:
            # FTS5 virtual tables do not inherit the relational cascade from
            # knowledge_chunks, so remove its rows explicitly before replacement.
            self.connection.execute("DELETE FROM knowledge_chunk_fts WHERE source_id=?", (source.source_id,))
            self.connection.execute("DELETE FROM knowledge_sources WHERE source_id=?", (source.source_id,))
            self.connection.execute(
                "INSERT INTO knowledge_sources VALUES(?,?,?,?,?,?)",
                (
                    source.source_id, source.project, source.title, source.visibility.value,
                    source.public_summary, str(source.public_url) if source.public_url else None,
                ),
            )
            if chunks:
                self.connection.executemany(
                    "INSERT INTO knowledge_chunks VALUES(?,?,?,?,?,?,?)",
                    [
                        (
                            chunk.chunk_id, chunk.source_id, chunk.ordinal, chunk.heading,
                            chunk.content, chunk.content_hash, json.dumps(chunk.embedding),
                        )
                        for chunk in chunks
                    ],
                )
                self.connection.executemany(
                    "INSERT INTO knowledge_chunk_fts(chunk_id,source_id,content) VALUES(?,?,?)",
                    [(chunk.chunk_id, chunk.source_id, chunk.content) for chunk in chunks],
                )

    def semantic_candidates(self) -> list[tuple[KnowledgeChunk, SourceMetadata]]:
        return self._load_matches("SELECT c.*, s.* FROM knowledge_chunks c JOIN knowledge_sources s USING(source_id)")

    def search_keywords(self, query: str, *, limit: int) -> list[RetrievalMatch]:
        tokens = _search_tokens(query)
        if not tokens:
            return []
        expression = " OR ".join(f'"{token}"' for token in tokens)
        rows = self.connection.execute(
            """
            SELECT c.*, s.*, bm25(knowledge_chunk_fts) AS score
            FROM knowledge_chunk_fts
            JOIN knowledge_chunks c ON c.chunk_id=knowledge_chunk_fts.chunk_id
            JOIN knowledge_sources s ON s.source_id=c.source_id
            WHERE knowledge_chunk_fts MATCH ?
            ORDER BY score, c.source_id, c.ordinal
            LIMIT ?
            """,
            (expression, limit),
        ).fetchall()
        return [
            RetrievalMatch(chunk=self._chunk_from_row(row), source=self._source_from_row(row), score=-row["score"], rank=index)
            for index, row in enumerate(rows, start=1)
        ]

    def count_chunks(self) -> int:
        return self.connection.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0]

    def _load_matches(self, sql: str) -> list[tuple[KnowledgeChunk, SourceMetadata]]:
        return [(self._chunk_from_row(row), self._source_from_row(row)) for row in self.connection.execute(sql)]

    @staticmethod
    def _chunk_from_row(row: sqlite3.Row) -> KnowledgeChunk:
        return KnowledgeChunk(
            chunk_id=row["chunk_id"], source_id=row["source_id"], ordinal=row["ordinal"],
            heading=row["heading"], content=row["content"], content_hash=row["content_hash"],
            embedding=json.loads(row["embedding_json"]),
        )

    @staticmethod
    def _source_from_row(row: sqlite3.Row) -> SourceMetadata:
        return SourceMetadata(
            source_id=row["source_id"], project=row["project"], title=row["title"],
            visibility=KnowledgeVisibility(row["visibility"]), public_summary=row["public_summary"],
            public_url=row["public_url"],
        )


def _search_tokens(query: str) -> list[str]:
    import re

    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]+|\d+", query.lower())
