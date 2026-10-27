"""Request-ID middleware (002 US8 ADDITION to the task list, ADR-0011 decision 3).

T217 expects `request_id` on log records but no task produced one. The
middleware generates (or accepts a well-formed inbound) `X-Request-ID`, binds it
to the log context, echoes it on the response, and writes one access line per
request that never contains headers, bodies or the query string.
"""

import logging
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.logging import get_log_context
from app.core.request_id import REQUEST_ID_HEADER, RequestIDMiddleware


@pytest.fixture
def client():
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)
    seen: list[dict] = []

    @app.get("/ok")
    def ok():  # sync -> runs in the threadpool, so context must propagate
        seen.append(get_log_context())
        return {"ok": True}

    @app.get("/boom")
    def boom():
        raise RuntimeError("kaboom")

    c = TestClient(app, raise_server_exceptions=False)
    c.seen = seen
    return c


def test_generates_a_request_id_and_echoes_it(client):
    response = client.get("/ok")
    request_id = response.headers[REQUEST_ID_HEADER]
    assert re.fullmatch(r"[0-9a-f]{32}", request_id)
    assert client.get("/ok").headers[REQUEST_ID_HEADER] != request_id


def test_accepts_a_well_formed_inbound_id(client):
    response = client.get("/ok", headers={REQUEST_ID_HEADER: "client-abc.123_X"})
    assert response.headers[REQUEST_ID_HEADER] == "client-abc.123_X"


@pytest.mark.parametrize("bad", ["has space", "x" * 65, "semi;colon", "<script>", ""])
def test_replaces_a_malformed_inbound_id(client, bad):
    response = client.get("/ok", headers={REQUEST_ID_HEADER: bad})
    assert response.headers[REQUEST_ID_HEADER] != bad
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers[REQUEST_ID_HEADER])


def test_request_id_is_bound_to_the_log_context_inside_the_handler(client):
    response = client.get("/ok")
    assert client.seen[-1]["request_id"] == response.headers[REQUEST_ID_HEADER]


def test_one_access_line_per_request_without_query_string(client, caplog):
    caplog.set_level(logging.INFO, logger="app.access")
    response = client.get("/ok?token=super-secret")
    records = [r for r in caplog.records if r.name == "app.access"]
    assert len(records) == 1
    record = records[0]
    assert (record.method, record.path, record.status) == ("GET", "/ok", 200)
    assert isinstance(record.duration_ms, (int, float)) and record.duration_ms >= 0
    assert record.request_id == response.headers[REQUEST_ID_HEADER]
    assert "super-secret" not in " ".join(str(v) for v in vars(record).values())


def test_unhandled_error_is_logged_as_500_and_still_carries_an_id(client, caplog):
    caplog.set_level(logging.INFO, logger="app.access")
    response = client.get("/boom")
    assert response.status_code == 500
    record = [r for r in caplog.records if r.name == "app.access"][-1]
    assert record.status == 500 and record.path == "/boom"


def test_unhandled_error_500_carries_the_same_id_as_the_access_line(client, caplog):
    caplog.set_level(logging.INFO, logger="app.access")
    response = client.get("/boom")
    assert response.status_code == 500
    record = [r for r in caplog.records if r.name == "app.access"][-1]
    assert response.headers[REQUEST_ID_HEADER] == record.request_id
    assert re.fullmatch(r"[0-9a-f]{32}", record.request_id)


def test_unhandled_error_500_echoes_a_well_formed_inbound_id(client):
    response = client.get("/boom", headers={REQUEST_ID_HEADER: "client-abc.123_X"})
    assert response.status_code == 500
    assert response.headers[REQUEST_ID_HEADER] == "client-abc.123_X"


def test_unhandled_error_is_still_raised_to_the_server_for_logging():
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/boom")
    def boom():
        raise RuntimeError("kaboom")

    with pytest.raises(RuntimeError, match="kaboom"):
        TestClient(app).get("/boom")


def test_the_real_app_has_the_middleware_mounted():
    from app.main import app

    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert REQUEST_ID_HEADER in response.headers
