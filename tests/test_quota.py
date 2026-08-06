"""Daily token budget: ledger, QuotaChatModel gate, and HTTP 429 behaviour."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from personal_agent.api.app import create_app
from personal_agent.application.quota import QuotaChatModel, QuotaExceededError, TokenQuotaStore
from personal_agent.wengraph_runtime import ChatMessage, ModelResponse


class _EchoModel:
    """Delegate that returns the user message length as text (deterministic cost)."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages: list[ChatMessage], **kwargs) -> ModelResponse:
        self.calls += 1
        return ModelResponse(text=messages[-1].content)


def _messages(text: str) -> list[ChatMessage]:
    return [ChatMessage(role="user", content=text)]


def test_store_persists_usage_across_reopens(tmp_path: Path) -> None:
    database = tmp_path / "quota.db"
    store = TokenQuotaStore(database)
    try:
        assert store.used_today() == 0
        store.add_tokens(1500)
        assert store.used_today() == 1500
    finally:
        store.close()

    reopened = TokenQuotaStore(database)
    try:
        assert reopened.used_today() == 1500  # 跨重启持久化
    finally:
        reopened.close()


def test_quota_model_charges_and_refuses_when_budget_exhausted(tmp_path: Path) -> None:
    store = TokenQuotaStore(tmp_path / "quota.db")
    delegate = _EchoModel()
    try:
        model = QuotaChatModel(delegate, store, daily_budget=1000)
        response = asyncio_run(model.complete(_messages("hello")))
        assert response.text == "hello"
        # 已记账：prompt（角色包装 4 + 内容）+ response 字符数
        assert store.used_today() == 4 + len("hello") + len("hello")

        # 第二次调用（估算 504）未超预算 → 成功，累计跨过 1000 门槛
        asyncio_run(model.complete(_messages("x" * 500)))
        assert delegate.calls == 2
        assert store.used_today() == 4 + 5 + 5 + 4 + 500 + 500

        # 预算已耗尽：任何新调用都被拒绝，且不触碰 delegate
        with pytest.raises(QuotaExceededError):
            asyncio_run(model.complete(_messages("hi")))
        assert delegate.calls == 2
    finally:
        store.close()


def test_submit_returns_429_when_quota_exhausted(tmp_path: Path) -> None:
    store = TokenQuotaStore(tmp_path / "quota.db")
    store.add_tokens(100)  # 已用 100 / 预算 100

    class _StubScheduler:
        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

        async def submit(self, *args, **kwargs):
            raise AssertionError("配额超限时不应提交到 scheduler")

    class _StubRunStore:
        pass

    client = TestClient(
        create_app(
            scheduler=_StubScheduler(),  # type: ignore[arg-type]
            run_store=_StubRunStore(),  # type: ignore[arg-type]
            quota_store=store,
            daily_token_budget=100,
        )
    )
    response = client.post("/api/conversations/tab-demo/messages", json={"question": "你好"})
    assert response.status_code == 429
    assert "预算已用尽" in response.json()["detail"]
    store.close()


def test_quota_endpoint_reports_usage_and_remaining(tmp_path: Path) -> None:
    store = TokenQuotaStore(tmp_path / "quota.db")
    try:
        store.add_tokens(250)
        client = TestClient(create_app(quota_store=store, daily_token_budget=1000))
        payload = client.get("/api/quota").json()
        assert payload == {
            "used_today": 250,
            "daily_budget": 1000,
            "remaining": 750,
            "exhausted": False,
        }

        client_no_store = TestClient(create_app())
        assert client_no_store.get("/api/quota").status_code == 503
    finally:
        store.close()


def test_distillation_does_not_touch_quota_store(tmp_path: Path) -> None:
    """蒸馏链路不包装 QuotaChatModel：配额文件不应出现于蒸馏运行。"""
    from personal_agent.distillation.runner import build_context

    ctx = build_context(
        input_dir=str(tmp_path / "raw"),
        data_dir=str(tmp_path / "data"),
        knowledge_database=str(tmp_path / "knowledge.db"),
        hash_embedding=True,
        chat_model=_EchoModel(),
    )
    try:
        assert not (tmp_path / "data" / "quota.db").exists()
    finally:
        ctx.close()


def asyncio_run(awaitable):
    import asyncio

    return asyncio.run(awaitable)
