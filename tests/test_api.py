from fastapi.testclient import TestClient

from personal_agent.api.app import create_app


def test_health_endpoint_returns_the_stable_contract() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "personal-agent"}
