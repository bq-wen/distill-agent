"""Memory hardening: per-conversation caps, LRU eviction, WAL, status endpoint."""

from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from personal_agent.api.app import create_app
from personal_agent.application.conversations import SQLiteConversationStore
from personal_agent.contracts import StatusResponse
from personal_agent.wengraph_runtime import ConversationEvent


def _event(conversation_id: str, index: int, created_at: datetime) -> ConversationEvent:
    return ConversationEvent(
        event_id=f"{conversation_id}-{index}",
        conversation_id=conversation_id,
        run_id=f"run-{index}",
        role="user",
        content=f"消息 {index}",
        created_at=created_at,
    )


def test_append_prunes_oldest_events_per_conversation(tmp_path: Path) -> None:
    store = SQLiteConversationStore(tmp_path / "conversations.db", max_events_per_conversation=3)
    try:
        base = datetime(2026, 8, 6, 10, 0)
        for i in range(5):
            store.append(_event("tab-1", i, base + timedelta(minutes=i)))
        events = store.list_all("tab-1")
        assert [e.event_id for e in events] == ["tab-1-2", "tab-1-3", "tab-1-4"]  # 最旧两条被淘汰
        assert store.count_events() == 3
    finally:
        store.close()


def test_cap_active_conversations_evicts_lru(tmp_path: Path) -> None:
    store = SQLiteConversationStore(tmp_path / "conversations.db", max_events_per_conversation=50)
    try:
        base = datetime(2026, 8, 6, 10, 0)
        for cid in ("old", "middle", "new"):
            store.append(_event(cid, 0, base))
        store.append(_event("new", 1, base + timedelta(hours=1)))  # new 最活跃
        store.append(_event("middle", 1, base + timedelta(minutes=30)))  # middle 次之
        assert store.count_active_conversations() == 3

        deleted = store.cap_active_conversations(2)
        assert deleted == 1
        assert store.count_active_conversations() == 2
        assert store.list_all("old") == []  # 最久未活动被清空
        assert len(store.list_all("middle")) == 2  # 活跃会话完整保留
        assert len(store.list_all("new")) == 2
    finally:
        store.close()


def test_wal_mode_is_enabled(tmp_path: Path) -> None:
    store = SQLiteConversationStore(tmp_path / "conversations.db")
    try:
        mode = store.connection.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        store.append(_event("tab", 0, datetime(2026, 8, 6, 10, 0)))
        store.wal_checkpoint()  # 不应抛错
    finally:
        store.close()


def test_status_endpoint_reports_capacity_snapshot(tmp_path: Path) -> None:
    store = SQLiteConversationStore(tmp_path / "conversations.db", max_events_per_conversation=50)
    store.append(_event("tab", 0, datetime(2026, 8, 6, 10, 0)))

    def provider() -> StatusResponse:
        return StatusResponse(
            queue_depth=3,
            active_conversations=1,
            conversation_events=1,
            runs=5,
            quota_used_today=100,
            quota_daily_budget=1000,
            quota_remaining=900,
            quota_exhausted=False,
            database_sizes={"conversations": store.database_size()},
        )

    client = TestClient(create_app(status_provider=provider))
    payload = client.get("/api/status").json()
    assert payload["queue_depth"] == 3
    assert payload["active_conversations"] == 1
    assert payload["quota_remaining"] == 900
    assert payload["database_sizes"]["conversations"] > 0
    store.close()


def test_delete_expired_before_physically_removes_old_runs(tmp_path: Path) -> None:
    from personal_agent.application.runs import PersonalRunStore, utc_now

    store = PersonalRunStore(tmp_path / "runs.db")
    try:
        now = utc_now()
        run = store.create_queued("tab", "旧问题")
        # 伪造 5 小时前的创建时间，才能命中 expire_before 的 cutoff
        store.connection.execute(
            "UPDATE personal_runs SET updated_at=? WHERE run_id=?",
            ((now - timedelta(hours=5)).isoformat(), run.run_id),
        )
        store.expire_before(now - timedelta(hours=3))
        assert store.count_runs() == 1  # 标记 EXPIRED，未删除

        store.delete_expired_before(now + timedelta(hours=1))
        assert store.count_runs() == 0  # EXPIRED 行被物理删除
    finally:
        store.close()
