import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from personal_agent.application.contracts import AgentAnswer
from personal_agent.application.runs import (
    PersonalRunScheduler,
    PersonalRunStatus,
    PersonalRunStore,
    QueueFullError,
    RunConflictError,
    utc_now,
)


class ControlledAnswerer:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started = asyncio.Event()

    async def answer(self, question: str, *, conversation_id: str) -> AgentAnswer:
        self.started.set()
        await self.release.wait()
        return AgentAnswer(text=f"answer: {question}")


def test_store_expires_old_runs_and_marks_restarted_running_run(tmp_path: Path) -> None:
    store = PersonalRunStore(tmp_path / "runs.db")
    record = store.create_queued("tab-1", "question")
    store.mark_running(record.run_id)

    assert store.mark_running_as_interrupted() == 1
    assert store.get(record.run_id).status is PersonalRunStatus.INTERRUPTED

    old = store.create_queued("tab-2", "old question")
    store.connection.execute(
        "UPDATE personal_runs SET updated_at=? WHERE run_id=?",
        ((utc_now() - timedelta(hours=25)).isoformat(), old.run_id),
    )
    store.connection.commit()
    assert store.expire_before(utc_now() - timedelta(hours=24)) == 1
    assert store.get(old.run_id).status is PersonalRunStatus.EXPIRED
    store.close()


def test_scheduler_limits_conversation_and_queue_then_persists_answer(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = PersonalRunStore(tmp_path / "runs.db")
        answerer = ControlledAnswerer()
        scheduler = PersonalRunScheduler(store, answerer, worker_count=1, max_queue_size=1)
        await scheduler.start()
        try:
            first = await scheduler.submit("tab-1", "first")
            await answerer.started.wait()
            with pytest.raises(RunConflictError):
                await scheduler.submit("tab-1", "again")

            second = await scheduler.submit("tab-2", "second")
            with pytest.raises(QueueFullError):
                await scheduler.submit("tab-3", "third")

            answerer.release.set()
            await scheduler.queue.join()
            assert store.get(first.run_id).status is PersonalRunStatus.COMPLETED
            assert store.get(first.run_id).answer.text == "answer: first"
            assert store.get(second.run_id).status is PersonalRunStatus.COMPLETED
        finally:
            await scheduler.stop()
            store.close()

    asyncio.run(scenario())


def test_start_recovers_all_persisted_queued_runs_with_a_bounded_queue(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = PersonalRunStore(tmp_path / "runs.db")
        first = store.create_queued("tab-1", "first")
        second = store.create_queued("tab-2", "second")
        answerer = ControlledAnswerer()
        scheduler = PersonalRunScheduler(store, answerer, worker_count=1, max_queue_size=1)
        await scheduler.start()
        try:
            await answerer.started.wait()
            answerer.release.set()
            await scheduler.queue.join()
            assert store.get(first.run_id).status is PersonalRunStatus.COMPLETED
            assert store.get(second.run_id).status is PersonalRunStatus.COMPLETED
        finally:
            await scheduler.stop()
            store.close()

    asyncio.run(scenario())
