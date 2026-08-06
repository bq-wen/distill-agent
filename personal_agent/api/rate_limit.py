"""Simple in-memory fixed-window rate limiter for the single-process API.

The deployment is bound to one Uvicorn worker, so a process-local limiter is
consistent with the capacity model documented in README. A shared scheduler
(Redis/Celery) would also need to host the limiter when scaling out.
"""

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class RateLimiter:
    """Fixed-window limiter keyed by client address; safe for one event loop.

    Distinct keys accumulate in memory until their window expires; the demo
    deployment is single-process and small, so this is acceptable. Call
    ``cleanup`` periodically when the key space may grow.
    """

    limit: int = 30
    window_seconds: float = 60.0
    _hits: dict[str, deque[float]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if self.limit < 1 or self.window_seconds <= 0:
            raise ValueError("limit 必须为正整数，window_seconds 必须为正数")

    def allow(self, key: str) -> bool:
        """Record one request and return whether it is within the limit."""

        now = time.monotonic()
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self.window_seconds:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True

    def cleanup(self) -> None:
        """Drop keys whose window has fully expired to bound memory growth."""

        now = time.monotonic()
        expired = [key for key, hits in self._hits.items() if not hits or now - hits[-1] >= self.window_seconds]
        for key in expired:
            del self._hits[key]
