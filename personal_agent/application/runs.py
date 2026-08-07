"""Persisted Run state and bounded single-process async scheduling."""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, Field

from personal_agent.application.contracts import AgentAnswer
from personal_agent.application.conversations import SQLiteConversationStore


def utc_now() -> datetime:
    return datetime.now(UTC)


class PersonalRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    EXPIRED = "expired"


ACTIVE_RUN_STATUSES = (PersonalRunStatus.QUEUED, PersonalRunStatus.RUNNING)


class PersonalRun(BaseModel):
    """Application-level Run record, safe to project into a future HTTP response."""

    run_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=2_000)
    status: PersonalRunStatus
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    answer: AgentAnswer | None = None
    error_message: str | None = None


class PersonalAgentAnswerer(Protocol):
    async def answer(self, question: str, *, conversation_id: str) -> AgentAnswer: ...


class RunConflictError(RuntimeError):
    """Raised when a conversation already has an active Run."""


class QueueFullError(RuntimeError):
    """Raised before persistence when the bounded in-memory dispatcher is full."""


class PersonalRunStore:
    """SQLite source of truth for queue lifecycle and terminal answers."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._lock = RLock()
        self.connection.execute("PRAGMA busy_timeout=5000")
        if self.path != Path(":memory:"):
            self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS personal_runs (
                run_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, question TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                completed_at TEXT, answer_json TEXT, error_message TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_personal_runs_conversation_status
                ON personal_runs(conversation_id, status);
            CREATE INDEX IF NOT EXISTS idx_personal_runs_updated_at ON personal_runs(updated_at);
            """
        )
        self.connection.commit()

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def create_queued(self, conversation_id: str, question: str) -> PersonalRun:
        with self._lock:
            active = self.connection.execute(
                "SELECT run_id FROM personal_runs WHERE conversation_id=? AND status IN (?, ?)",
                (conversation_id, *(status.value for status in ACTIVE_RUN_STATUSES)),
            ).fetchone()
            if active is not None:
                raise RunConflictError(f"会话已有未完成 Run: {active['run_id']}")
            now = utc_now()
            record = PersonalRun(
                run_id=f"personal-{uuid4()}", conversation_id=conversation_id, question=question,
                status=PersonalRunStatus.QUEUED, created_at=now, updated_at=now,
            )
            with self.connection:
                self.connection.execute(
                    "INSERT INTO personal_runs VALUES(?,?,?,?,?,?,?,?,?)", self._values(record)
                )
        return record

    def get(self, run_id: str) -> PersonalRun | None:
        with self._lock:
            row = self.connection.execute("SELECT * FROM personal_runs WHERE run_id=?", (run_id,)).fetchone()
        return self._from_row(row) if row else None

    def list_by_status(self, status: PersonalRunStatus) -> list[PersonalRun]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM personal_runs WHERE status=? ORDER BY created_at, run_id", (status.value,)
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def mark_running(self, run_id: str) -> PersonalRun:
        return self._transition(run_id, PersonalRunStatus.RUNNING)

    def mark_completed(self, run_id: str, answer: AgentAnswer) -> PersonalRun:
        return self._transition(run_id, PersonalRunStatus.COMPLETED, answer=answer)

    def mark_failed(self, run_id: str, message: str) -> PersonalRun:
        return self._transition(run_id, PersonalRunStatus.FAILED, error_message=message)

    def mark_running_as_interrupted(self) -> int:
        now = utc_now()
        with self._lock, self.connection:
            cursor = self.connection.execute(
                "UPDATE personal_runs SET status=?, updated_at=?, completed_at=?, error_message=? WHERE status=?",
                (
                    PersonalRunStatus.INTERRUPTED.value, now.isoformat(), now.isoformat(),
                    "服务进程在 Run 执行期间退出，请重新提交问题", PersonalRunStatus.RUNNING.value,
                ),
            )
        return cursor.rowcount

    def expire_before(self, cutoff: datetime) -> int:
        now = utc_now()
        with self._lock, self.connection:
            cursor = self.connection.execute(
                "UPDATE personal_runs SET status=?, updated_at=?, completed_at=?, answer_json=NULL, error_message=? "
                "WHERE updated_at<? AND status!=?",
                (
                    PersonalRunStatus.EXPIRED.value, now.isoformat(), now.isoformat(), "临时会话已过期",
                    cutoff.isoformat(), PersonalRunStatus.EXPIRED.value,
                ),
            )
        return cursor.rowcount

    def _transition(
        self,
        run_id: str,
        target: PersonalRunStatus,
        *,
        answer: AgentAnswer | None = None,
        error_message: str | None = None,
    ) -> PersonalRun:
        with self._lock:
            row = self.connection.execute("SELECT * FROM personal_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError(f"未找到 Run: {run_id}")
            existing = self._from_row(row)
            now = utc_now()
            completed_at = now if target in {PersonalRunStatus.COMPLETED, PersonalRunStatus.FAILED} else None
            updated = existing.model_copy(
                update={
                    "status": target, "updated_at": now, "completed_at": completed_at,
                    "answer": answer, "error_message": error_message,
                }
            )
            with self.connection:
                self.connection.execute(
                    "UPDATE personal_runs SET status=?, updated_at=?, completed_at=?, answer_json=?, error_message=? WHERE run_id=?",
                    (
                        updated.status.value, updated.updated_at.isoformat(),
                        updated.completed_at.isoformat() if updated.completed_at else None,
                        updated.answer.model_dump_json() if updated.answer else None, updated.error_message, run_id,
                    ),
                )
        return updated

    @staticmethod
    def _values(record: PersonalRun) -> tuple:
        return (
            record.run_id, record.conversation_id, record.question, record.status.value,
            record.created_at.isoformat(), record.updated_at.isoformat(), None, None, None,
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> PersonalRun:
        return PersonalRun(
            run_id=row["run_id"], conversation_id=row["conversation_id"], question=row["question"],
            status=PersonalRunStatus(row["status"]), created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            answer=AgentAnswer.model_validate_json(row["answer_json"]) if row["answer_json"] else None,
            error_message=row["error_message"],
        )


class PersonalRunScheduler:
    """Bounded async dispatcher for exactly one FastAPI process and one event loop."""

    def __init__(
        self,
        store: PersonalRunStore,
        answerer: PersonalAgentAnswerer,
        *,
        worker_count: int = 2,
        max_queue_size: int = 20,
        ttl: timedelta = timedelta(hours=24),
        conversation_store: SQLiteConversationStore | None = None,
        cleanup_interval: timedelta = timedelta(minutes=30),
    ) -> None:
        if worker_count < 1 or max_queue_size < 1 or ttl <= timedelta():
            raise ValueError("worker_count、max_queue_size 和 ttl 必须为正数")
        if cleanup_interval <= timedelta():
            raise ValueError("cleanup_interval 必须为正数")
        self.store = store
        self.answerer = answerer
        self.ttl = ttl
        self.cleanup_interval = cleanup_interval
        self.conversation_store = conversation_store
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=max_queue_size)
        self._worker_count = worker_count
        self._workers: list[asyncio.Task] = []
        self._cleanup_task: asyncio.Task | None = None
        self._admission_lock = asyncio.Lock()
        self._scheduled_run_ids: set[str] = set()

    async def start(self) -> None:
        if self._workers:
            return
        self._run_cleanup()
        self.store.mark_running_as_interrupted()
        self._fill_queue_from_store()
        self._workers = [asyncio.create_task(self._worker(), name=f"personal-agent-worker-{index}") for index in range(self._worker_count)]
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(), name="personal-agent-cleanup")

    async def stop(self) -> None:
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            await asyncio.gather(self._cleanup_task, return_exceptions=True)
            self._cleanup_task = None
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []

    async def _cleanup_loop(self) -> None:
        """Periodic expiry so long-running processes do not accumulate stale rows."""

        while True:
            await asyncio.sleep(self.cleanup_interval.total_seconds())
            self._run_cleanup()

    def _run_cleanup(self) -> None:
        """Expire runs and conversation events older than the TTL."""

        cutoff = utc_now() - self.ttl
        self.store.expire_before(cutoff)
        if self.conversation_store is not None:
            self.conversation_store.expire_before(cutoff)

    async def submit(self, conversation_id: str, question: str) -> PersonalRun:
        if not self._workers:
            raise RuntimeError("调度器尚未启动")
        async with self._admission_lock:
            if self.queue.full():
                raise QueueFullError("服务当前繁忙，请稍后重试")
            record = self.store.create_queued(conversation_id, question)
            self.queue.put_nowait(record.run_id)
            self._scheduled_run_ids.add(record.run_id)
            return record

    async def _worker(self) -> None:
        while True:
            run_id = await self.queue.get()
            try:
                record = self.store.get(run_id)
                if record is None or record.status is not PersonalRunStatus.QUEUED:
                    continue
                self.store.mark_running(run_id)
                try:
                    answer = await self.answerer.answer(record.question, conversation_id=record.conversation_id)
                except asyncio.CancelledError:
                    raise
                except Exception as error:  # noqa: BLE001 - worker 容错：任何失败都标记为 run failed 供前端展示
                    self.store.mark_failed(run_id, str(error) or type(error).__name__)
                else:
                    self.store.mark_completed(run_id, answer)
            finally:
                self._scheduled_run_ids.discard(run_id)
                self._fill_queue_from_store()
                self.queue.task_done()

    def _fill_queue_from_store(self) -> None:
        """Refill bounded memory scheduling from durable queued records after restart."""

        for record in self.store.list_by_status(PersonalRunStatus.QUEUED):
            if self.queue.full():
                return
            if record.run_id in self._scheduled_run_ids:
                continue
            self.queue.put_nowait(record.run_id)
            self._scheduled_run_ids.add(record.run_id)
