"""Daily token budget enforcement for the serving graph (anti cost-burn).

The WenGraph runtime does not report token usage on responses, so the ledger
uses the runtime's own ``TokenEstimator`` (character-based by default) to
estimate prompt + response tokens. The estimate is conservative enough for a
coarse daily budget that protects the deployed API key.
"""

import sqlite3
import threading
from datetime import date
from pathlib import Path

from personal_agent.wengraph_runtime import (
    CharacterTokenEstimator,
    ChatMessage,
    ChatModel,
    ModelResponse,
    TokenEstimator,
)


class QuotaExceededError(Exception):
    """The daily token budget is exhausted; refuse further LLM calls."""


class TokenQuotaStore:
    """Persistent per-day token accounting backed by SQLite."""

    def __init__(self, database: str | Path) -> None:
        self._path = Path(database)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(str(self._path), check_same_thread=False)
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS quota_log (day TEXT PRIMARY KEY, tokens_used INTEGER NOT NULL)"
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def database_size(self) -> int:
        """主库文件字节数。"""
        return self._path.stat().st_size if self._path.is_file() else 0

    def used_today(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT tokens_used FROM quota_log WHERE day = ?", (str(date.today()),)
            ).fetchone()
        return int(row[0]) if row else 0

    def add_tokens(self, tokens: int) -> int:
        with self._lock:
            self._connection.execute(
                "INSERT INTO quota_log (day, tokens_used) VALUES (?, ?) "
                "ON CONFLICT(day) DO UPDATE SET tokens_used = tokens_used + excluded.tokens_used",
                (str(date.today()), tokens),
            )
            self._connection.commit()
            row = self._connection.execute(
                "SELECT tokens_used FROM quota_log WHERE day = ?", (str(date.today()),)
            ).fetchone()
        return int(row[0]) if row else 0

    def try_reserve(self, tokens: int, limit: int) -> bool:
        """原子 reserve：锁内检查 + 扣减，并发下也不会双双通过。

        Returns False 时不做任何扣减（调用方应拒绝该请求）。
        """
        with self._lock:
            row = self._connection.execute(
                "SELECT tokens_used FROM quota_log WHERE day = ?", (str(date.today()),)
            ).fetchone()
            used = int(row[0]) if row else 0
            if used + tokens > limit:
                return False
            self._connection.execute(
                "INSERT INTO quota_log (day, tokens_used) VALUES (?, ?) "
                "ON CONFLICT(day) DO UPDATE SET tokens_used = tokens_used + excluded.tokens_used",
                (str(date.today()), tokens),
            )
            self._connection.commit()
        return True


class QuotaChatModel(ChatModel):
    """Wrap a delegate ChatModel with a daily token budget.

    The prompt cost is reserved (and charged) before the call so concurrent
    requests cannot both pass the check; the response cost is settled after
    the call since its length is unknown upfront.
    """

    def __init__(
        self,
        delegate: ChatModel,
        store: TokenQuotaStore,
        *,
        daily_budget: int,
        estimator: TokenEstimator | None = None,
    ) -> None:
        self._delegate = delegate
        self._store = store
        self._daily_budget = daily_budget
        self._estimator = estimator or CharacterTokenEstimator()

    async def complete(self, messages: list[ChatMessage], **kwargs) -> ModelResponse:
        prompt_tokens = self._estimator.count_messages(messages)
        # 原子 reserve：检查与扣减在同一把锁内，两个并发请求不可能同时通过。
        if not self._store.try_reserve(prompt_tokens, self._daily_budget):
            raise QuotaExceededError("今日 token 预算已用尽，请明日再来")
        response = await self._delegate.complete(messages, **kwargs)
        # 结算响应 token：预算拒绝的是“新请求”，实际响应量如实入账，
        # 可能导致当日总额略超预算（估算精度），但不影响拒绝语义。
        self._store.add_tokens(self._estimator.count_text(response.text or ""))
        return response

    async def aclose(self) -> None:
        await self._delegate.aclose()
