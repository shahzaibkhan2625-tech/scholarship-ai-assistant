"""010 T362: `POST /scholarships/match-url` cannot be steered to an internal address (FR-SSRF-1/5/8,
SC-002). The real `fetch_url` runs; only the wire (a recording `httpx.MockTransport`) and the
resolver are faked, so a request that reaches `wire.requests` is a connection that would have been
made. Runs in the rolled-back DB transaction."""

import json
import logging
import socket
from unittest.mock import patch

import httpx
import pytest

from app.core.logging import JsonFormatter
from app.models.scholarship import Scholarship
from app.tools import web_fetch
from app.tools.extract_requirements import ExtractedScholarshipData

PUBLIC_IP = "93.184.216.34"
DNS = {
    "scholarships.example.com": [PUBLIC_IP],
    "redirector.example.com": [PUBLIC_IP],
    "meta.example.com": ["169.254.169.254"],
}


def _resolve(host: str) -> list[str]:
    if host in DNS:
        return list(DNS[host])
    raise socket.gaierror(host)


@pytest.fixture
def wire(monkeypatch):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers["Host"] == "redirector.example.com":
            return httpx.Response(302, headers={"Location": "http://10.0.0.5/internal"})
        return httpx.Response(200, text="<html><body>Scholarship details</body></html>")

    real = web_fetch._make_client
    monkeypatch.setattr(web_fetch, "_make_client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(web_fetch, "_resolve_host", _resolve)
    return requests


@pytest.fixture
def log_lines():
    lines: list[dict] = []

    class Capture(logging.Handler):
        def emit(self, record):
            lines.append(json.loads(JsonFormatter().format(record)))  # context is read at format time

    handler = Capture(level=logging.WARNING)
    logger = logging.getLogger("app.workflows.url_match.graph")
    logger.addHandler(handler)
    try:
        yield lines
    finally:
        logger.removeHandler(handler)


def _scholarship_count(db_session_factory) -> int:
    with db_session_factory() as session:
        return session.query(Scholarship).count()


@pytest.mark.parametrize(
    "url,reason_fragment",
    [
        ("http://169.254.169.254/latest/meta-data/", "169.254.169.254"),
        ("http://127.0.0.1/admin", "127.0.0.1"),
        ("http://meta.example.com/x", "169.254.169.254"),
        ("http://224.0.0.1/", "224.0.0.1"),
        ("http://redirector.example.com/start", "10.0.0.5"),
    ],
)
def test_refused_url_is_422_blocked_with_no_row_and_no_request_to_the_target(
    authed_user, db_session_factory, wire, log_lines, url, reason_fragment
):
    client, headers = authed_user["client"], authed_user["headers"]
    before = _scholarship_count(db_session_factory)

    response = client.post("/scholarships/match-url", headers=headers, json={"url": url})

    assert response.status_code == 422
    body = response.json()
    assert body["reported_as"] == "fetch_failure"
    assert body["error"].startswith("blocked:")
    assert reason_fragment in body["error"]
    assert "verdict" not in body
    assert _scholarship_count(db_session_factory) == before
    assert all(request.url.host == PUBLIC_IP for request in wire)  # nothing but the public first hop

    blocked = [line for line in log_lines if line.get("entry_point") == "match-url"]
    assert len(blocked) == 1
    assert blocked[0]["user_id"] == str(authed_user["user_id"])
    assert reason_fragment in blocked[0]["message"] or reason_fragment in blocked[0].get("reason", "")
    assert "latest/meta-data" not in json.dumps(blocked[0])  # host and reason only, no path or content


def test_public_url_still_returns_the_normal_result(authed_user, wire):
    client, headers = authed_user["client"], authed_user["headers"]
    extracted = ExtractedScholarshipData(name="Public Scholarship", requirements=[])
    with (
        patch("app.workflows.url_match.graph.extract_requirements_from_page", return_value=extracted),
        patch("app.workflows.url_match.graph.ingest_html_page", return_value=0),
    ):
        response = client.post(
            "/scholarships/match-url", headers=headers, json={"url": "https://scholarships.example.com/a"}
        )

    assert response.status_code == 200
    assert response.json()["official_source"] == "https://scholarships.example.com/a"
    assert [r.url.host for r in wire] == [PUBLIC_IP]
