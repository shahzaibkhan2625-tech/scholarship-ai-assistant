"""003 T280 (FR-SKIP-3, research R-06): optional request headers on `safe_get` / `fetch_url`, and 304
handling. No real network: a recording MockTransport through the module's `_make_client` seam, and an
injected resolver (same harness as 010's SSRF battery)."""

import httpx
import pytest

from app.tools import web_fetch
from tests.tools.test_web_fetch_ssrf import DNS, PUBLIC_IP, resolver  # noqa: F401  (shared harness)

CONDITIONAL = {"If-None-Match": '"abc"', "If-Modified-Since": "Wed, 26 Nov 2026 10:00:00 GMT"}


class Recorder:
    def __init__(self, handler):
        self.requests: list[httpx.Request] = []
        self._handler = handler
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


@pytest.fixture
def install(monkeypatch):
    def _install(handler) -> Recorder:
        recorder = Recorder(handler)
        real = web_fetch._make_client
        monkeypatch.setattr(web_fetch, "_make_client", lambda **kw: real(transport=recorder.transport, **kw))
        return recorder

    return _install


def fetch(url, **kwargs):
    return web_fetch.fetch_url(url, resolve=resolver, **kwargs)


def test_headers_are_sent_on_the_first_request(install):
    recorder = install(lambda request: httpx.Response(200, text="<html>ok</html>"))
    fetch("https://a.example.com/list", headers=CONDITIONAL)
    sent = recorder.requests[0].headers
    assert sent["If-None-Match"] == '"abc"' and sent["If-Modified-Since"].startswith("Wed, 26 Nov 2026")
    assert sent["User-Agent"].startswith("ScholarshipAIAssistant")  # the default headers are kept


def test_headers_are_never_forwarded_to_a_redirect_target(install):
    def handler(request):
        if request.headers["Host"] == "a.example.com":
            return httpx.Response(302, headers={"Location": "https://b.example.com/list"})
        return httpx.Response(200, text="<html>moved</html>")

    recorder = install(handler)
    result = fetch("https://a.example.com/list", headers=CONDITIONAL)
    assert result.success and result.final_url == "https://b.example.com/list"
    first, second = recorder.requests
    assert "If-None-Match" in first.headers
    assert "If-None-Match" not in second.headers and "If-Modified-Since" not in second.headers


def test_a_refused_url_is_still_refused_when_headers_are_present(install):
    recorder = install(lambda request: httpx.Response(200, text="ok"))
    result = fetch("https://internal.example.com/", headers=CONDITIONAL)  # resolves to a private address
    assert result.success is False and result.blocked is True
    assert recorder.requests == []  # nothing reached the wire


def test_a_blocked_fetch_is_a_site_failure_never_a_provider_outcome(install):
    install(lambda request: httpx.Response(200, text="ok"))
    result = fetch("https://metadata.example.com/", headers=CONDITIONAL)
    assert result.blocked is True and result.error.startswith("blocked: ")
    assert "provider" not in result.error.lower()


def test_304_is_reported_as_not_modified_with_no_body(install):
    install(lambda request: httpx.Response(304))
    result = fetch("https://a.example.com/list", headers=CONDITIONAL)
    assert result.success is True and result.not_modified is True
    assert result.status_code == 304 and result.html is None


def test_a_server_that_ignores_if_modified_since_and_returns_200_is_handled(install):
    install(lambda request: httpx.Response(200, text="<html>full body</html>", headers={"Last-Modified": "now"}))
    result = fetch("https://a.example.com/list", headers=CONDITIONAL)
    assert result.success and result.not_modified is False and result.html == "<html>full body</html>"


def test_validators_from_the_response_are_returned(install):
    install(
        lambda request: httpx.Response(
            200, text="<html>x</html>",
            headers={"ETag": '"v1"', "Last-Modified": "Wed, 26 Nov 2026 09:00:00 GMT"},
        )
    )
    result = fetch("https://a.example.com/list")
    assert result.etag == '"v1"' and result.last_modified == "Wed, 26 Nov 2026 09:00:00 GMT"


def test_without_headers_nothing_changes(install):
    recorder = install(lambda request: httpx.Response(200, text="<html>ok</html>"))
    result = fetch("https://a.example.com/list")
    assert result.success and result.not_modified is False and result.etag is None
    assert "If-None-Match" not in recorder.requests[0].headers


def test_safe_get_accepts_headers_for_the_first_request_only(install):
    recorder = install(lambda request: httpx.Response(200, text="ok"))
    response, final_url = web_fetch.safe_get("https://a.example.com/x", headers=CONDITIONAL, resolve=resolver)
    assert response.status_code == 200 and final_url == "https://a.example.com/x"
    assert recorder.requests[0].headers["If-None-Match"] == '"abc"'
