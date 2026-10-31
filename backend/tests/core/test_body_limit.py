"""010 T394: the body-limit middleware rejects oversized upload requests (FR-UPLOAD-1)."""

import asyncio
import json

import pytest
from starlette.testclient import TestClient

from app.core.body_limit import BodyLimitMiddleware

LIMIT, ALLOWANCE = 1000, 100
UPLOAD_PATHS = ["/profile/import-cv", "/applications/6f1c0c1e-0000-0000-0000-000000000000/documents"]


class _Counter:
    """Bare ASGI app: reads the whole body and reports how much it saw."""

    def __init__(self):
        self.calls = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        self.calls += 1
        size = 0
        while True:
            message = await receive()
            size += len(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body = json.dumps({"size": size}).encode()
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})


@pytest.fixture
def app():
    return _Counter()


@pytest.fixture
def client(app):
    return TestClient(BodyLimitMiddleware(app, max_upload_bytes=LIMIT, allowance=ALLOWANCE))


@pytest.mark.parametrize("path", UPLOAD_PATHS)
def test_declared_content_length_over_the_limit_is_413_before_the_app_runs(client, app, path):
    response = client.post(path, content=b"x" * (LIMIT + ALLOWANCE + 1))
    assert response.status_code == 413
    assert response.json() == {"detail": "File too large (maximum 10 MB)"}
    assert app.calls == 0


@pytest.mark.parametrize("path", UPLOAD_PATHS)
def test_body_at_the_limit_plus_allowance_passes(client, path):
    response = client.post(path, content=b"x" * (LIMIT + ALLOWANCE))
    assert response.status_code == 200 and response.json() == {"size": LIMIT + ALLOWANCE}


@pytest.mark.parametrize("path", UPLOAD_PATHS)
def test_chunked_body_is_413_once_the_limit_is_crossed(client, path):
    def chunks():
        for _ in range(30):
            yield b"x" * 50  # 1500 bytes, no Content-Length

    response = client.post(path, content=chunks())
    assert response.status_code == 413
    assert response.json() == {"detail": "File too large (maximum 10 MB)"}


def test_other_paths_are_not_limited(client):
    response = client.post("/auth/login", content=b"x" * 5000)
    assert response.status_code == 200 and response.json() == {"size": 5000}


def test_small_upload_bodies_are_untouched(client):
    assert client.post("/profile/import-cv", content=b"x" * 10).json() == {"size": 10}


def test_similar_paths_are_not_matched(client):
    for path in ("/profile/import-cv/extra", "/applications/a/b/documents", "/applications/documents"):
        assert client.post(path, content=b"x" * 5000).status_code == 200


def test_non_http_scopes_pass_through():
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    asyncio.run(BodyLimitMiddleware(inner, max_upload_bytes=1)({"type": "lifespan"}, None, None))
    assert seen == ["lifespan"]
