"""In-memory per-client token-bucket rate limiting (002 US8 T225, NFR-HARDEN-2, ADR-0011 decision 1).

Pure ASGI, like `request_id.py`. The client key is `scope["client"][0]`. Behind Caddy that
is `127.0.0.1` for every caller unless uvicorn runs with `--proxy-headers
--forwarded-allow-ips=127.0.0.1`, which rewrites it to the real client address before this
middleware runs; the app deliberately never parses `X-Forwarded-For` itself.

State is per process and lost on restart; with N workers the effective limit is N times
`RATE_LIMIT_PER_MINUTE`. A Postgres- or Redis-backed limiter was rejected (ADR-0011).
Only `/health` is exempt. Non-HTTP scopes (lifespan, websocket) are not limited.
"""

import math
import threading
import time
from typing import Callable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

__all__ = ["TokenBucketLimiter", "RateLimitMiddleware"]

EXEMPT_PATHS = frozenset({"/health"})
UNKNOWN_CLIENT = "unknown"
DEFAULT_MAX_BUCKETS = 10_000


class TokenBucketLimiter:
    """Capacity `limit_per_minute`, refilling `limit_per_minute / 60` tokens per second; a
    request costs one token. `limit_per_minute` is a plain attribute so tests can lower it."""

    def __init__(
        self,
        limit_per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_buckets: int = DEFAULT_MAX_BUCKETS,
    ) -> None:
        self.limit_per_minute = limit_per_minute
        self._clock = clock
        self._max_buckets = max_buckets
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_seen)
        self._lock = threading.Lock()

    @property
    def _rate(self) -> float:
        return self.limit_per_minute / 60.0

    def check(self, key: str) -> int:
        """Spends one token for `key`. Returns 0 if allowed, otherwise the whole number of
        seconds (>= 1) until a token is available."""
        with self._lock:
            now = self._clock()
            capacity = float(self.limit_per_minute)
            entry = self._buckets.get(key)
            if entry is None:
                if len(self._buckets) >= self._max_buckets:
                    self._make_room(now)
                tokens = capacity
            else:
                tokens = min(capacity, entry[0] + (now - entry[1]) * self._rate)

            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                return 0
            self._buckets[key] = (tokens, now)
            return max(1, math.ceil((1.0 - tokens) / self._rate))

    def _make_room(self, now: float) -> None:
        """Called under the lock when a new key would exceed the cap. A bucket that has
        refilled to capacity is indistinguishable from an absent one, so those go first;
        only if that is not enough is the least recently seen client evicted."""
        capacity = float(self.limit_per_minute)
        full = [k for k, (tokens, seen) in self._buckets.items() if tokens + (now - seen) * self._rate >= capacity]
        for k in full:
            del self._buckets[k]
        while len(self._buckets) >= self._max_buckets:
            oldest = min(self._buckets, key=lambda k: self._buckets[k][1])
            del self._buckets[oldest]

    def bucket_count(self) -> int:
        with self._lock:
            return len(self._buckets)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp, limiter: TokenBucketLimiter) -> None:
        self.app = app
        self.limiter = limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        retry_after = self.limiter.check(client[0] if client else UNKNOWN_CLIENT)
        if retry_after:
            response = JSONResponse(
                {
                    "detail": f"Rate limit exceeded. Retry after {retry_after} seconds.",
                    "retry_after_seconds": retry_after,
                },
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
