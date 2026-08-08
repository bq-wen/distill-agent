from datetime import timedelta

import pytest

from personal_agent.settings import ApplicationSettings


def test_settings_use_data_directory_and_validate_capacity(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PERSONAL_AGENT_QUEUE_WORKERS", "3")
    monkeypatch.setenv("PERSONAL_AGENT_CONVERSATION_TTL_HOURS", "24")

    settings = ApplicationSettings.from_environment()

    assert settings.knowledge_database == tmp_path / "state" / "knowledge.db"
    assert settings.queue_workers == 3
    assert settings.conversation_ttl == timedelta(hours=24)


def test_settings_reject_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONAL_AGENT_QUEUE_SIZE", "0")

    with pytest.raises(ValueError, match="QUEUE_SIZE"):
        ApplicationSettings.from_environment()
