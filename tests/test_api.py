import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from personal_agent.application.contracts import AgentAnswer
from personal_agent.application.runs import PersonalRunScheduler, PersonalRunStore
from personal_agent.api.app import create_app
from personal_agent.knowledge.documents import parse_markdown_document
from personal_agent.knowledge.embedding import HashEmbeddingProvider
from personal_agent.knowledge.retrieval import PersonalKnowledgeService
from personal_agent.knowledge.store import KnowledgeStore


def test_health_endpoint_returns_the_stable_contract() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "personal-agent"}


def _indexed_profile_store(path: Path) -> KnowledgeStore:
    store = KnowledgeStore(path)
    document_path = Path(__file__).parents[1] / "knowledge" / "profile.md"
    service = PersonalKnowledgeService(store, HashEmbeddingProvider())
    service.index_document(parse_markdown_document(document_path))
    return store


def test_profile_endpoint_returns_identity_from_profile_document(tmp_path: Path) -> None:
    store = _indexed_profile_store(tmp_path / "knowledge.db")
    try:
        with TestClient(create_app(knowledge_store=store)) as client:
            response = client.get("/api/profile")
    finally:
        store.close()

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Wen"
    assert body["monogram"] == "W"
    assert body["github"] == "https://github.com/bq-wen"
    assert "WenGraph" in body["covered_topics"]


def test_profile_endpoint_404_without_profile_document(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path / "knowledge.db")
    try:
        with TestClient(create_app(knowledge_store=store)) as client:
            response = client.get("/api/profile")
    finally:
        store.close()

    assert response.status_code == 404


def test_profile_endpoint_503_without_knowledge_store() -> None:
    response = TestClient(create_app()).get("/api/profile")

    assert response.status_code == 503


def test_topics_endpoint_returns_grouped_questions(tmp_path: Path) -> None:
    store = _indexed_profile_store(tmp_path / "knowledge.db")
    try:
        with TestClient(create_app(knowledge_store=store)) as client:
            response = client.get("/api/topics")
    finally:
        store.close()

    assert response.status_code == 200
    body = response.json()
    assert any(group["project"] == "Personal" for group in body)
    questions = [
        question
        for group in body
        for topic in group["topics"]
        for question in topic["questions"]
    ]
    assert "你的知识覆盖哪些主题？" in questions


def test_topics_endpoint_empty_without_sources(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path / "knowledge.db")
    try:
        with TestClient(create_app(knowledge_store=store)) as client:
            response = client.get("/api/topics")
    finally:
        store.close()

    assert response.status_code == 200
    assert response.json() == []


def test_app_can_serve_built_frontend_entry(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<main>Personal Agent</main>", encoding="utf-8")

    with TestClient(create_app(static_directory=frontend)) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Personal Agent" in response.text


class ImmediateAnswerer:
    async def answer(self, question: str, *, conversation_id: str) -> AgentAnswer:
        return AgentAnswer(text=f"answer: {question}")


def test_message_submission_and_run_polling_contract(tmp_path: Path) -> None:
    store = PersonalRunStore(tmp_path / "runs.db")
    scheduler = PersonalRunScheduler(store, ImmediateAnswerer(), worker_count=1, max_queue_size=2)
    with TestClient(create_app(scheduler=scheduler, run_store=store)) as client:
        response = client.post("/api/conversations/tab-1/messages", json={"question": "介绍 WenGraph"})

        assert response.status_code == 202
        assert response.json()["status"] in {"queued", "running"}
        run_id = response.json()["run_id"]
        for _ in range(20):
            result = client.get(f"/api/runs/{run_id}")
            if result.json()["status"] == "completed":
                break
            asyncio.run(asyncio.sleep(0.01))
        assert result.status_code == 200
        assert result.json() == {
            "run_id": run_id,
            "status": "completed",
            "answer": {"text": "answer: 介绍 WenGraph", "citations": []},
            "error_message": None,
        }
    store.close()


class BlockingAnswerer:
    async def answer(self, question: str, *, conversation_id: str) -> AgentAnswer:
        await asyncio.Future()
        raise AssertionError("unreachable")


def test_api_validates_input_and_translates_scheduler_errors(tmp_path: Path) -> None:
    store = PersonalRunStore(tmp_path / "runs.db")
    scheduler = PersonalRunScheduler(store, BlockingAnswerer(), worker_count=1, max_queue_size=1)
    with TestClient(create_app(scheduler=scheduler, run_store=store)) as client:
        assert client.post("/api/conversations/tab-1/messages", json={"question": ""}).status_code == 422
        first = client.post("/api/conversations/tab-1/messages", json={"question": "one"})
        assert first.status_code == 202
        assert client.post("/api/conversations/tab-1/messages", json={"question": "two"}).status_code == 409
        assert client.post("/api/conversations/tab-2/messages", json={"question": "two"}).status_code == 202
        assert client.post("/api/conversations/tab-3/messages", json={"question": "three"}).status_code == 429
        assert client.get("/api/runs/missing").status_code == 404
    store.close()
