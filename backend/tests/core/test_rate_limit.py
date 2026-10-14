"""Rate-limit tests (T216, NFR-HARDEN-2, SC-007, ADR-0011 decision 1).

The middleware is exercised directly as raw ASGI with an injected fake clock, so no
test sleeps and no test touches the real app's limiter (except the two `app` tests at
the bottom, which only assert wiring). The client key is `scope["client"][0]`; behind
the proxy uvicorn's `--proxy-headers` has already rewritten it, so the app never parses
`X-Forwarded-For` itself.
"""

import asyncio
import json

from app.core.rate_limit import RateLimitMiddleware, TokenBucketLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _call(middleware, *, client="203.0.113.7", path="/anything", scope_type="http"):
    """Drives one request through the middleware; returns (status, headers dict, body)."""
    scope = {"type": scope_type, "method": "GET", "path": path, "headers": [], "client": (client, 5000) if client else None}
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(middleware(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    return start["status"], headers, body


def _make(limit=3, clock=None, **kwargs):
    clock = clock or FakeClock()
    limiter = TokenBucketLimiter(limit, clock=clock, **kwargs)
    return RateLimitMiddleware(_ok_app, limiter=limiter), limiter, clock


def test_requests_within_the_limit_pass():
    mw, _, _ = _make(limit=3)
    assert [_call(mw)[0] for _ in range(3)] == [200, 200, 200]


def test_request_beyond_the_limit_gets_429_with_json_body_and_retry_after():
    mw, _, _ = _make(limit=3)
    for _ in range(3):
        _call(mw)

    status, headers, body = _call(mw)

    assert status == 429
    assert headers["content-type"].startswith("application/json")
    assert int(headers["retry-after"]) >= 1
    payload = json.loads(body)
    assert "rate limit" in payload["detail"].lower()
    assert payload["retry_after_seconds"] == int(headers["retry-after"])


def test_a_second_client_ip_is_unaffected():
    mw, _, _ = _make(limit=2)
    for _ in range(2):
        _call(mw, client="198.51.100.1")
    assert _call(mw, client="198.51.100.1")[0] == 429

    assert _call(mw, client="198.51.100.2")[0] == 200


def test_the_bucket_refills_with_time():
    mw, _, clock = _make(limit=60)  # 1 token per second
    for _ in range(60):
        _call(mw)
    assert _call(mw)[0] == 429

    clock.now += 1.0

    assert _call(mw)[0] == 200
    assert _call(mw)[0] == 429  # only one token came back


def test_retry_after_reflects_time_until_the_next_token():
    mw, _, clock = _make(limit=6)  # 1 token per 10 seconds
    for _ in range(6):
        _call(mw)
    clock.now += 4.0  # 0.4 of a token back: 6 more seconds needed

    _, headers, _ = _call(mw)

    assert headers["retry-after"] == "6"


def test_health_is_exempt_even_when_the_bucket_is_empty():
    mw, _, _ = _make(limit=1)
    _call(mw)
    assert _call(mw)[0] == 429

    assert _call(mw, path="/health")[0] == 200
    assert _call(mw, path="/health")[0] == 200


def test_only_the_exact_health_path_is_exempt():
    mw, _, _ = _make(limit=1)
    _call(mw)

    assert _call(mw, path="/health/deep")[0] == 429
    assert _call(mw, path="/docs")[0] == 429


def test_non_http_scopes_pass_through_untouched():
    mw, _, _ = _make(limit=1)
    called = []

    async def lifespan_app(scope, receive, send):
        called.append(scope["type"])

    mw.app = lifespan_app
    asyncio.run(mw({"type": "lifespan"}, None, None))

    assert called == ["lifespan"]


def test_a_missing_client_shares_one_unknown_bucket():
    mw, _, _ = _make(limit=1)
    assert _call(mw, client=None)[0] == 200
    assert _call(mw, client=None)[0] == 429


def test_bucket_table_stays_bounded_and_evicts_without_resetting_active_clients():
    mw, limiter, _ = _make(limit=5, max_buckets=10)
    for i in range(50):
        _call(mw, client=f"192.0.2.{i}")

    assert limiter.bucket_count() <= 10


def test_full_buckets_are_purged_before_active_ones_are_evicted():
    mw, limiter, clock = _make(limit=60, max_buckets=3)
    for ip in ("10.0.0.1", "10.0.0.2", "10.0.0.3"):
        _call(mw, client=ip)
    clock.now += 5.0  # every bucket is back to full: all are indistinguishable from absent

    _call(mw, client="10.0.0.4")

    assert limiter.bucket_count() == 1


def test_reset_clears_all_state():
    mw, limiter, _ = _make(limit=1)
    _call(mw)
    assert _call(mw)[0] == 429

    limiter.reset()

    assert _call(mw)[0] == 200


def test_the_real_app_mounts_the_limiter_and_a_429_still_carries_a_request_id():
    from fastapi.testclient import TestClient

    from app.main import app, rate_limiter

    original = rate_limiter.limit_per_minute
    rate_limiter.reset()
    rate_limiter.limit_per_minute = 2
    try:
        client = TestClient(app)
        assert client.get("/health").status_code == 200  # exempt: costs nothing
        statuses = [client.get("/auth/me-does-not-exist").status_code for _ in range(3)]
        response = client.get("/auth/me-does-not-exist")
    finally:
        rate_limiter.limit_per_minute = original
        rate_limiter.reset()

    assert statuses[:2] == [404, 404] and statuses[2] == 429
    assert response.status_code == 429
    assert response.headers["x-request-id"]  # RequestIDMiddleware is outermost
    assert response.headers["retry-after"]
