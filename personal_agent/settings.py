"""Validated environment configuration for the deployable application."""

import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path


def _positive_int(name: str, default: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} 必须为正整数")
    return value


def _score(name: str, default: float) -> float:
    value = float(os.environ.get(name, str(default)))
    if not -1 <= value <= 1:
        raise ValueError(f"{name} 必须在 -1 到 1 之间")
    return value


@dataclass(frozen=True, slots=True)
class ApplicationSettings:
    """Settings deliberately limited to deployment and capacity concerns."""

    data_directory: Path
    knowledge_database: Path
    runs_database: Path
    conversations_database: Path
    quota_database: Path
    embedding_model: str
    embedding_device: str | None
    queue_workers: int
    queue_size: int
    conversation_ttl: timedelta
    minimum_semantic_score: float
    rate_limit_per_minute: int
    daily_token_budget: int

    @classmethod
    def from_environment(cls) -> "ApplicationSettings":
        data_directory = Path(os.environ.get("PERSONAL_AGENT_DATA_DIR", "data"))
        ttl_hours = _positive_int("PERSONAL_AGENT_CONVERSATION_TTL_HOURS", 24)
        embedding_device = os.environ.get("PERSONAL_AGENT_EMBEDDING_DEVICE", "cpu").strip() or None
        embedding_model = os.environ.get("PERSONAL_AGENT_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5").strip()
        if not embedding_model:
            raise ValueError("PERSONAL_AGENT_EMBEDDING_MODEL 不能为空")
        return cls(
            data_directory=data_directory,
            knowledge_database=Path(os.environ.get("PERSONAL_AGENT_KNOWLEDGE_DB", data_directory / "knowledge.db")),
            runs_database=Path(os.environ.get("PERSONAL_AGENT_RUNS_DB", data_directory / "runs.db")),
            conversations_database=Path(
                os.environ.get("PERSONAL_AGENT_CONVERSATIONS_DB", data_directory / "conversations.db")
            ),
            quota_database=Path(os.environ.get("PERSONAL_AGENT_QUOTA_DB", data_directory / "quota.db")),
            embedding_model=embedding_model,
            embedding_device=embedding_device,
            queue_workers=_positive_int("PERSONAL_AGENT_QUEUE_WORKERS", 2),
            queue_size=_positive_int("PERSONAL_AGENT_QUEUE_SIZE", 20),
            conversation_ttl=timedelta(hours=ttl_hours),
            minimum_semantic_score=_score("PERSONAL_AGENT_MINIMUM_SEMANTIC_SCORE", 0.35),
            rate_limit_per_minute=_positive_int("PERSONAL_AGENT_RATE_LIMIT_PER_MINUTE", 30),
            daily_token_budget=_positive_int("PERSONAL_AGENT_DAILY_TOKEN_BUDGET", 20_000),
        )
