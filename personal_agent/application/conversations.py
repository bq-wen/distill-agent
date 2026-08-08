"""SQLite-backed WenGraph conversation store for temporary browser-tab memory.

Capacity guards (anti-bloat / anti-abuse) live here so a public deployment
cannot grow without bound:

- WAL journal mode with periodic checkpoints (see ``wal_checkpoint``).
- Per-conversation event cap: the oldest events are pruned on append.
- Active-conversation cap: ``cap_active_conversations`` evicts the least
  recently active conversations (LRU), keeping busy visitors intact.
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock

from personal_agent.wengraph_runtime import ConversationEvent, ConversationStore


class SQLiteConversationStore(ConversationStore):
    """Keeps role/content events; cleanup owns TTL + capacity retention policy."""

    def __init__(self, path: str | Path, *, max_events_per_conversation: int = 50) -> None:
        if max_events_per_conversation < 1:
            raise ValueError("max_events_per_conversation 必须至少为 1")
        self.path = Path(path)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self._lock = RLock()
        self._max_events_per_conversation = max_events_per_conversation
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
                (
                    event.event_id,
                    event.conversation_id,
                    event.run_id,
                    event.role,
                    event.content,
                    event.created_at.isoformat(),
                ),
            )
            # 单会话事件数上限：保留最新 max_events 条，淘汰最旧。
            self.connection.execute(
                "DELETE FROM conversation_events WHERE conversation_id=? AND event_id NOT IN ("
                "  SELECT event_id FROM conversation_events WHERE conversation_id=? "
                "  ORDER BY created_at DESC, event_id DESC LIMIT ?"
                ")",
                (event.conversation_id, event.conversation_id, self._max_events_per_conversation),
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
            cursor = self.connection.execute(
                "DELETE FROM conversation_events WHERE created_at<?", (cutoff.isoformat(),)
            )
        return cursor.rowcount

    def count_active_conversations(self) -> int:
        """会话数 = 仍保留任何事件的 conversation_id 数量。"""
        with self._lock:
            row = self.connection.execute(
                "SELECT COUNT(DISTINCT conversation_id) AS n FROM conversation_events"
            ).fetchone()
        return int(row["n"]) if row else 0

    def count_events(self) -> int:
        with self._lock:
            row = self.connection.execute(
                "SELECT COUNT(*) AS n FROM conversation_events"
            ).fetchone()
        return int(row["n"]) if row else 0

    def cap_active_conversations(self, limit: int) -> int:
        """LRU 驱逐：只保留最近活跃的 limit 个会话，删除其余会话的全部事件。"""
        if limit < 1:
            raise ValueError("limit 必须至少为 1")
        with self._lock, self.connection:
            cursor = self.connection.execute(
                "DELETE FROM conversation_events WHERE conversation_id IN ("
                "  SELECT conversation_id FROM ("
                "    SELECT conversation_id, MAX(created_at) AS last_active FROM conversation_events"
                "    GROUP BY conversation_id ORDER BY last_active DESC LIMIT -1 OFFSET ?"
                "  )"
                ")",
                (limit,),
            )
        return cursor.rowcount

    def wal_checkpoint(self) -> None:
        with self._lock:
            self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def database_size(self) -> int:
        """主库文件字节数（不含 WAL；checkpoint 后 WAL 会并入）。"""
        return self.path.stat().st_size if self.path.is_file() else 0

    def close(self) -> None:
        with self._lock:
            self.wal_checkpoint()
            self.connection.close()

    @staticmethod
    def _event(row: sqlite3.Row) -> ConversationEvent:
        return ConversationEvent(
            event_id=row["event_id"],
            conversation_id=row["conversation_id"],
            run_id=row["run_id"],
            role=row["role"],
            content=row["content"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
