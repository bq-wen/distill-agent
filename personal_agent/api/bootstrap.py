"""Production dependency composition. HTTP handlers remain in ``app.py``."""

from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI

from personal_agent.api.app import create_app
from personal_agent.api.rate_limit import RateLimiter
from personal_agent.application.conversations import SQLiteConversationStore
from personal_agent.application.quota import QuotaChatModel, TokenQuotaStore
from personal_agent.application.runs import PersonalRunScheduler, PersonalRunStore
from personal_agent.application.service import PersonalAgentService
from personal_agent.contracts import StatusResponse
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
    quota_store: TokenQuotaStore

    async def close(self) -> None:
        await self.chat_model.aclose()
        self.quota_store.close()
        self.knowledge_store.close()
        self.conversation_store.close()
        self.run_store.close()


def build_resources(settings: ApplicationSettings) -> ProductionResources:
    settings.data_directory.mkdir(parents=True, exist_ok=True)
    knowledge_store = KnowledgeStore(settings.knowledge_database)
    conversation_store = SQLiteConversationStore(
        settings.conversations_database, max_events_per_conversation=settings.max_events_per_conversation
    )
    run_store = PersonalRunStore(settings.runs_database)
    quota_store = TokenQuotaStore(settings.quota_database)
    model = QuotaChatModel(
        OpenAIChatModel(OpenAIChatConfig.from_environment()),
        quota_store,
        daily_budget=settings.daily_token_budget,
    )
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
        max_active_conversations=settings.max_active_conversations,
    )
    return ProductionResources(
        scheduler, run_store, knowledge_store, conversation_store, model, quota_store=quota_store
    )


def create_production_app(settings: ApplicationSettings | None = None) -> FastAPI:
    """Uvicorn factory. Run with ``--factory`` so secrets are read at startup."""

    resolved_settings = settings or ApplicationSettings.from_environment()
    resources = build_resources(resolved_settings)
    static_directory = Path(__file__).parents[2] / "frontend_dist"

    def _status() -> StatusResponse:
        used = resources.quota_store.used_today()
        return StatusResponse(
            queue_depth=resources.scheduler.queue_depth(),
            active_conversations=resources.scheduler.active_conversations(),
            conversation_events=resources.conversation_store.count_events(),
            runs=resources.run_store.count_runs(),
            quota_used_today=used,
            quota_daily_budget=resolved_settings.daily_token_budget,
            quota_remaining=max(resolved_settings.daily_token_budget - used, 0),
            quota_exhausted=used >= resolved_settings.daily_token_budget,
            database_sizes={
                "knowledge": resources.knowledge_store.database_size(),
                "runs": resources.run_store.database_size(),
                "conversations": resources.conversation_store.database_size(),
                "quota": resources.quota_store.database_size(),
            },
        )

    return create_app(
        scheduler=resources.scheduler,
        run_store=resources.run_store,
        knowledge_store=resources.knowledge_store,
        close_resources=resources.close,
        static_directory=static_directory if static_directory.is_dir() else None,
        rate_limiter=RateLimiter(limit=resolved_settings.rate_limit_per_minute),
        quota_store=resources.quota_store,
        daily_token_budget=resolved_settings.daily_token_budget,
        status_provider=_status,
    )
