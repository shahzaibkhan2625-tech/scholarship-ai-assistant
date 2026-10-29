"""`api_connector` tool tests (T076) — the connector is monkeypatched so
these exercise only the tool's output shaping, not governance (covered in
tests/sources/test_connectors.py)."""

import socket
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx

from app.sources.connectors.api_connector import ApiFetchResult, _default_client
from app.tools import api_connector as api_connector_tool_module
from app.tools import web_fetch


def test_api_connector_tool_shapes_success_result(monkeypatch):
    source_id = uuid.uuid4()
    fetched_at = datetime.now(timezone.utc)

    def fake_fetch_api(_db, _source_id, _query):
        return ApiFetchResult(
            source_id=source_id,
            query="masters scholarships",
            success=True,
            records=[{"name": "Test Scholarship"}],
            http_status=200,
            fetched_at=fetched_at,
        )

    monkeypatch.setattr(api_connector_tool_module, "fetch_api", fake_fetch_api)

    output = api_connector_tool_module.api_connector_tool(db=None, source_id=source_id, query="masters scholarships")

    assert output.status == "ok"
    assert output.records == [{"name": "Test Scholarship"}]
    assert output.source_id == source_id
    assert output.fetched_at == fetched_at.isoformat()


def test_api_connector_tool_shapes_failure_result(monkeypatch):
    source_id = uuid.uuid4()

    def fake_fetch_api(_db, _source_id, _query):
        return ApiFetchResult(
            source_id=source_id,
            query="masters scholarships",
            success=False,
            records=[],
            http_status=500,
            error="API unavailable",
        )

    monkeypatch.setattr(api_connector_tool_module, "fetch_api", fake_fetch_api)

    output = api_connector_tool_module.api_connector_tool(db=None, source_id=source_id, query="masters scholarships")

    assert output.status == "fail"
    assert output.records == []
    assert output.error == "API unavailable"


# --- 010 T361: the default transport goes through the safe fetch (no real network) ---------


_REAL_MAKE_CLIENT = web_fetch._make_client


def _install_wire(monkeypatch, handler, *, addresses=("93.184.216.34",)):
    """Route `web_fetch`'s client through a recording MockTransport and a fixed resolver."""
    requests: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(
        web_fetch, "_make_client", lambda **kw: _REAL_MAKE_CLIENT(transport=httpx.MockTransport(recording), **kw)
    )
    monkeypatch.setattr(web_fetch, "_resolve_host", lambda host: list(addresses))
    return requests


def test_default_transport_refuses_a_registry_domain_resolving_to_a_non_public_address(monkeypatch):
    requests = _install_wire(monkeypatch, lambda r: httpx.Response(200, json={"results": []}), addresses=("10.0.0.5",))
    raw = _default_client(SimpleNamespace(domain="api.example.com"), "masters")
    assert raw.success is False
    assert raw.error.startswith("blocked: ")
    assert "10.0.0.5" in raw.error
    assert requests == []


def test_default_transport_does_not_follow_redirects(monkeypatch):
    requests = _install_wire(
        monkeypatch, lambda r: httpx.Response(302, headers={"Location": "https://other.example.com/"})
    )
    raw = _default_client(SimpleNamespace(domain="api.example.com"), "masters")
    assert raw.success is False
    assert raw.error.startswith("blocked: ")
    assert len(requests) == 1


def test_default_transport_still_works_for_a_public_domain(monkeypatch):
    requests = _install_wire(monkeypatch, lambda r: httpx.Response(200, json={"results": [{"name": "S"}]}))
    raw = _default_client(SimpleNamespace(domain="api.example.com"), "masters")
    assert raw.success is True
    assert raw.status_code == 200
    assert raw.records == [{"name": "S"}]
    (request,) = requests
    assert request.url.host == "93.184.216.34"
    assert request.headers["Host"] == "api.example.com"
    assert request.extensions["sni_hostname"] == "api.example.com"
    assert request.url.params["q"] == "masters"


def test_default_transport_keeps_its_failure_shapes(monkeypatch):
    _install_wire(monkeypatch, lambda r: httpx.Response(503))
    raw = _default_client(SimpleNamespace(domain="api.example.com"), "q")
    assert (raw.success, raw.status_code, raw.error) == (False, 503, "API responded with HTTP 503")

    def timeout(_request):
        raise httpx.ReadTimeout("slow")

    _install_wire(monkeypatch, timeout)
    assert _default_client(SimpleNamespace(domain="api.example.com"), "q").error == "Request timed out: slow"

    def unresolvable(_host):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(web_fetch, "_resolve_host", unresolvable)
    raw = _default_client(SimpleNamespace(domain="api.example.com"), "q")
    assert raw.success is False and raw.error.startswith("Could not reach API: ")
