"""SQLite-backed WenGraph conversation store for temporary browser-tab memory."""

import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock

from personal_agent.wengraph_runtime import ConversationEvent, ConversationStore


class SQLiteConversationStore(ConversationStore):
    """Keeps only role/content events; application cleanup owns the 24-hour retention policy."""

    def __init__(self, path: str | Path) -> None:
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._lock = RLock()
        with self.connection:
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS conversation_events (
                event_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, run_id TEXT NOT NULL,
                role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL)"""
            )
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_conversation_time "
                "ON conversation_events(conversation_id, created_at, event_id)"
            )

    def append(self, event: ConversationEvent) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                "INSERT INTO conversation_events VALUES(?,?,?,?,?,?)",
                (event.event_id, event.conversation_id, event.run_id, event.role, event.content, event.created_at.isoformat()),
            )

    def list_recent(self, conversation_id: str, limit: int) -> list[ConversationEvent]:
        if limit < 1:
            raise ValueError("limit 必须至少为 1")
        return self.list_all(conversation_id)[-limit:]

    def list_all(self, conversation_id: str) -> list[ConversationEvent]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM conversation_events WHERE conversation_id=? ORDER BY created_at, event_id",
                (conversation_id,),
            ).fetchall()
        return [self._event(row) for row in rows]

    def expire_before(self, cutoff: datetime) -> int:
        with self._lock, self.connection:
            cursor = self.connection.execute("DELETE FROM conversation_events WHERE created_at<?", (cutoff.isoformat(),))
        return cursor.rowcount

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    @staticmethod
    def _event(row: sqlite3.Row) -> ConversationEvent:
        return ConversationEvent(
            event_id=row["event_id"], conversation_id=row["conversation_id"], run_id=row["run_id"],
            role=row["role"], content=row["content"], created_at=datetime.fromisoformat(row["created_at"]),
        )
