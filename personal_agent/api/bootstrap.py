"""Production dependency composition. HTTP handlers remain in ``app.py``."""

from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI

from personal_agent.api.app import create_app
from personal_agent.application.conversations import SQLiteConversationStore
from personal_agent.application.runs import PersonalRunScheduler, PersonalRunStore
from personal_agent.application.service import PersonalAgentService
from personal_agent.knowledge.embedding import SentenceTransformersEmbeddingProvider
from personal_agent.knowledge.retrieval import PersonalKnowledgeService
from personal_agent.knowledge.store import KnowledgeStore
from personal_agent.settings import ApplicationSettings
from personal_agent.wengraph_runtime import OpenAIChatConfig, OpenAIChatModel


@dataclass(slots=True)
class ProductionResources:
    """Long-lived resources owned by exactly one FastAPI worker process."""

    scheduler: PersonalRunScheduler
    run_store: PersonalRunStore
    knowledge_store: KnowledgeStore
    conversation_store: SQLiteConversationStore
    chat_model: OpenAIChatModel

    async def close(self) -> None:
        await self.chat_model.aclose()
        self.knowledge_store.close()
        self.conversation_store.close()
        self.run_store.close()


def build_resources(settings: ApplicationSettings) -> ProductionResources:
    settings.data_directory.mkdir(parents=True, exist_ok=True)
    knowledge_store = KnowledgeStore(settings.knowledge_database)
    conversation_store = SQLiteConversationStore(settings.conversations_database)
    run_store = PersonalRunStore(settings.runs_database)
    model = OpenAIChatModel(OpenAIChatConfig.from_environment())
    knowledge = PersonalKnowledgeService(
        knowledge_store,
        SentenceTransformersEmbeddingProvider(settings.embedding_model, device=settings.embedding_device),
    )
    service = PersonalAgentService(
        knowledge,
        model,
        minimum_semantic_score=settings.minimum_semantic_score,
        conversation_store=conversation_store,
    )
    scheduler = PersonalRunScheduler(
        run_store,
        service,
        worker_count=settings.queue_workers,
        max_queue_size=settings.queue_size,
        ttl=settings.conversation_ttl,
        conversation_store=conversation_store,
    )
    return ProductionResources(scheduler, run_store, knowledge_store, conversation_store, model)


def create_production_app(settings: ApplicationSettings | None = None) -> FastAPI:
    """Uvicorn factory. Run with ``--factory`` so secrets are read at startup."""

    resources = build_resources(settings or ApplicationSettings.from_environment())
    static_directory = Path(__file__).parents[2] / "frontend_dist"
    return create_app(
        scheduler=resources.scheduler,
        run_store=resources.run_store,
        close_resources=resources.close,
        static_directory=static_directory if static_directory.is_dir() else None,
    )
