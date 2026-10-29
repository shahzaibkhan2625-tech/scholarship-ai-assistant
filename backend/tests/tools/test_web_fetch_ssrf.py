"""010 T359: the SSRF battery for `app.tools.web_fetch.fetch_url`.

No real network. Every request goes to a recording `httpx.MockTransport` through the
module's `_make_client` seam, and every lookup goes through an injected resolver. The
transport is the "wire": a request that reaches it is a connection that would have been
made, so a blocked hop must leave NO trace in `requests` for that hop.
"""

import logging
import socket

import httpx
import pytest

from app.core.url_safety import _non_public
from app.tools import web_fetch
from app.tools.web_fetch import FetchResult, fetch_url

PUBLIC_IP = "93.184.216.34"
PUBLIC_IP_2 = "93.184.216.35"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"

# name -> addresses; anything not listed does not resolve.
DNS = {
    "a.example.com": [PUBLIC_IP],
    "b.example.com": [PUBLIC_IP_2],
    "v6.example.com": [PUBLIC_V6],
    "multi.example.com": [PUBLIC_IP, "10.0.0.5"],
    "metadata.example.com": ["169.254.169.254"],
    "internal.example.com": ["192.168.0.10"],
    # POSIX inet_aton spellings of 127.0.0.1 (getaddrinfo accepts them, ipaddress does not)
    "2130706433": ["127.0.0.1"],
    "0x7f.1": ["127.0.0.1"],
    "017700000001": ["127.0.0.1"],
}


def resolver(host: str) -> list[str]:
    if host in DNS:
        return list(DNS[host])
    raise socket.gaierror(f"cannot resolve {host}")


class Wire:
    """A recording transport. `routes` maps (Host header, path) -> a response factory or a
    status; unmatched requests get 200 'ok'."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get((request.headers["Host"], request.url.path))
        if route is None:
            return httpx.Response(200, text="<html>ok</html>")
        if isinstance(route, Exception):
            raise route
        status, location = route
        headers = {"Location": location} if location else {}
        return httpx.Response(status, headers=headers, text="" if location else "body")

    @property
    def connected_hosts(self) -> list[str]:
        return [r.url.host for r in self.requests]


@pytest.fixture
def wire(monkeypatch):
    def install(routes=None) -> Wire:
        w = Wire(routes)
        real = web_fetch._make_client

        def make_client(**kwargs):
            return real(transport=w.transport, **kwargs)

        monkeypatch.setattr(web_fetch, "_make_client", make_client)
        return w

    return install


def fetch(url, **kwargs):
    return fetch_url(url, resolve=resolver, **kwargs)


def assert_blocked(result: FetchResult, url: str):
    assert result.success is False
    assert result.blocked is True
    assert result.error and result.error.startswith("blocked: ")
    assert result.url == url
    assert result.status_code is None
    assert result.html is None


def assert_no_non_public_connection(w: Wire):
    for request in w.requests:
        assert not _non_public(request.url.host), f"connected to non-public {request.url.host}"


# --- blocked at the first URL: zero requests reach the transport -------------------------

BLOCKED_FIRST_URL = [
    "http://127.0.0.1/",
    "http://localhost/",
    "http://app.localhost/",
    "http://[::1]/",
    "http://10.1.2.3/",
    "http://172.16.5.5/",
    "http://192.168.1.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[fd00::1]/",
    "http://[::ffff:10.0.0.1]/",
    "http://2130706433/",
    "http://0x7f.1/",
    "http://017700000001/",
    "http://0.0.0.0/",
    "http://[::]/",
    "http://100.64.0.1/",
    "http://224.0.0.1/",
    "http://239.255.255.250/",
    "http://[ff02::1]/",
    "http://multi.example.com/",  # DNS: one public, one private address
    "http://metadata.example.com/",  # a name that resolves to the metadata address
    "file:///etc/passwd",
    "ftp://a.example.com/x",
    "gopher://a.example.com/",
]


@pytest.mark.parametrize("url", BLOCKED_FIRST_URL)
def test_first_url_blocked_before_any_request(wire, url):
    w = wire()
    result = fetch(url)
    assert_blocked(result, url)
    assert w.requests == []


# --- blocked at a redirect hop: the hop itself is never requested ------------------------


def _chain(public_hops: int, final_location: str) -> dict:
    """`public_hops` public redirects (a.example.com/0 -> /1 ...), then a redirect to
    `final_location`."""
    routes = {}
    for i in range(public_hops):
        routes[("a.example.com", f"/{i}")] = (302, f"http://a.example.com/{i + 1}")
    routes[("a.example.com", f"/{public_hops}")] = (302, final_location)
    return routes


REDIRECT_TARGETS = [
    ("hop1-loopback", 0, "http://127.0.0.1/admin"),
    ("hop1-metadata", 0, "http://169.254.169.254/latest/meta-data/"),
    ("hop1-name-resolving-private", 0, "http://internal.example.com/"),
    ("hop1-multicast", 0, "http://224.0.0.1/"),
    ("hop2-rfc1918", 1, "http://10.0.0.1/"),
    ("hop2-ipv6-loopback", 1, "http://[::1]/"),
    ("last-allowed-hop", 4, "http://192.168.0.1/"),
    ("to-file-scheme", 0, "file:///etc/passwd"),
    ("to-ftp-scheme", 1, "ftp://a.example.com/x"),
    ("numeric-encoding", 0, "http://2130706433/"),
]


@pytest.mark.parametrize("name,public_hops,target", REDIRECT_TARGETS, ids=[c[0] for c in REDIRECT_TARGETS])
def test_redirect_to_non_public_is_never_requested(wire, name, public_hops, target):
    w = wire(_chain(public_hops, target))
    url = "http://a.example.com/0"
    result = fetch(url)
    assert_blocked(result, url)
    # exactly the public hops were requested, and every one went to the pinned public IP
    assert len(w.requests) == public_hops + 1
    assert set(w.connected_hosts) == {PUBLIC_IP}
    assert_no_non_public_connection(w)


def test_scheme_downgrade_https_to_http_is_blocked(wire):
    w = wire({("a.example.com", "/"): (302, "http://a.example.com/plain")})
    url = "https://a.example.com/"
    result = fetch(url)
    assert_blocked(result, url)
    assert "downgrade" in result.error
    assert len(w.requests) == 1


def test_sixth_redirect_hop_is_refused(wire):
    routes = {("a.example.com", f"/{i}"): (302, f"http://a.example.com/{i + 1}") for i in range(10)}
    w = wire(routes)
    url = "http://a.example.com/0"
    result = fetch(url)
    assert_blocked(result, url)
    assert "too many redirects" in result.error
    assert len(w.requests) == 6  # the original + 5 allowed hops; the 6th redirect is not followed


def test_redirect_limit_follows_settings(wire, monkeypatch):
    monkeypatch.setattr(web_fetch.settings, "max_fetch_redirects", 0)
    routes = {("a.example.com", "/0"): (302, "http://a.example.com/1")}
    w = wire(routes)
    result = fetch("http://a.example.com/0")
    assert_blocked(result, "http://a.example.com/0")
    assert len(w.requests) == 1


def test_zero_redirects_param_refuses_the_first_redirect(wire):
    w = wire({("a.example.com", "/0"): (301, "http://a.example.com/1")})
    result = fetch("http://a.example.com/0", max_redirects=0)
    assert_blocked(result, "http://a.example.com/0")
    assert len(w.requests) == 1


# --- pinning --------------------------------------------------------------------------------


def test_https_request_is_pinned_with_host_header_and_sni(wire):
    w = wire()
    result = fetch("https://a.example.com/path?q=1")
    assert result.success is True
    (request,) = w.requests
    assert request.url.host == PUBLIC_IP
    assert str(request.url) == f"https://{PUBLIC_IP}/path?q=1"
    assert request.headers["Host"] == "a.example.com"
    assert request.extensions["sni_hostname"] == "a.example.com"


def test_explicit_port_is_kept_in_url_and_host_header(wire):
    w = wire()
    fetch("https://a.example.com:8443/x")
    (request,) = w.requests
    assert request.url.host == PUBLIC_IP and request.url.port == 8443
    assert request.headers["Host"] == "a.example.com:8443"
    assert request.extensions["sni_hostname"] == "a.example.com"


def test_http_request_is_pinned_without_sni(wire):
    w = wire()
    fetch("http://a.example.com/")
    (request,) = w.requests
    assert request.url.host == PUBLIC_IP
    assert request.headers["Host"] == "a.example.com"
    assert "sni_hostname" not in request.extensions


def test_ipv6_address_is_pinned_in_brackets(wire):
    w = wire()
    result = fetch("https://v6.example.com/x")
    assert result.success is True
    (request,) = w.requests
    assert request.url.host == PUBLIC_V6
    assert str(request.url) == f"https://[{PUBLIC_V6}]/x"
    assert request.headers["Host"] == "v6.example.com"


def test_public_ip_literal_is_requested_as_is_without_sni(wire):
    w = wire()
    result = fetch(f"https://{PUBLIC_IP}/x")
    assert result.success is True
    (request,) = w.requests
    assert request.url.host == PUBLIC_IP
    assert "sni_hostname" not in request.extensions


def test_each_hop_is_pinned_to_its_own_checked_address(wire):
    w = wire({("a.example.com", "/"): (301, "https://b.example.com/next")})
    fetch("https://a.example.com/")
    assert w.connected_hosts == [PUBLIC_IP, PUBLIC_IP_2]
    assert [r.headers["Host"] for r in w.requests] == ["a.example.com", "b.example.com"]
    assert [r.extensions["sni_hostname"] for r in w.requests] == ["a.example.com", "b.example.com"]


def test_client_ignores_proxy_environment_and_does_not_follow_redirects(monkeypatch):
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(var, "http://proxy.invalid:3128")
    client = web_fetch._make_client(timeout=3.0)
    try:
        assert client.trust_env is False
        assert client.follow_redirects is False
        assert client.headers["User-Agent"] == web_fetch._USER_AGENT
    finally:
        client.close()


# --- public happy paths ----------------------------------------------------------------------


def test_public_happy_path_with_two_redirects(wire):
    w = wire(
        {
            ("a.example.com", "/start"): (301, "https://b.example.com/middle"),
            ("b.example.com", "/middle"): (302, "https://a.example.com/final"),
            ("a.example.com", "/final"): (200, None),
        }
    )
    result = fetch("https://a.example.com/start")
    assert result == FetchResult(
        url="https://a.example.com/start",
        success=True,
        status_code=200,
        html="body",
        final_url="https://a.example.com/final",
        blocked=False,
    )
    assert len(w.requests) == 3
    assert_no_non_public_connection(w)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_all_redirect_statuses_are_followed(wire, status):
    w = wire({("a.example.com", "/old"): (status, "/new")})
    result = fetch("http://a.example.com/old")
    assert result.success is True
    assert result.final_url == "http://a.example.com/new"
    assert [r.method for r in w.requests] == ["GET", "GET"]


@pytest.mark.parametrize(
    "location,expected",
    [
        ("/abs/path", "https://a.example.com/abs/path"),
        ("rel", "https://a.example.com/dir/rel"),
        ("//b.example.com/proto-relative", "https://b.example.com/proto-relative"),
    ],
)
def test_relative_location_is_resolved_against_the_current_url(wire, location, expected):
    wire({("a.example.com", "/dir/start"): (302, location)})
    result = fetch("https://a.example.com/dir/start")
    assert result.success is True
    assert result.final_url == expected


def test_redirect_without_location_is_returned_as_the_response(wire):
    wire({("a.example.com", "/"): (302, None)})
    result = fetch("http://a.example.com/")
    assert result.success is True
    assert result.status_code == 302
    assert result.final_url == "http://a.example.com/"


# --- unchanged non-blocked shapes ----------------------------------------------------------


def test_unresolvable_host_is_unreachable_not_blocked(wire):
    w = wire()
    result = fetch("https://nope.example.com/x")
    assert result.success is False
    assert result.blocked is False
    assert result.error.startswith("Could not reach URL: ")
    assert (result.status_code, result.final_url, result.html) == (None, None, None)
    assert w.requests == []


def test_unresolvable_redirect_target_is_unreachable_not_blocked(wire):
    wire({("a.example.com", "/"): (302, "http://nope.example.com/")})
    result = fetch("http://a.example.com/")
    assert result.blocked is False
    assert result.error.startswith("Could not reach URL: ")


def test_timeout_shape_is_unchanged(wire):
    wire({("a.example.com", "/slow"): httpx.ReadTimeout("took too long")})
    result = fetch("https://a.example.com/slow")
    assert result == FetchResult(
        url="https://a.example.com/slow", success=False, error="Request timed out: took too long"
    )


def test_connect_error_shape_is_unchanged(wire):
    wire({("a.example.com", "/down"): httpx.ConnectError("connection refused")})
    result = fetch("https://a.example.com/down")
    assert result == FetchResult(
        url="https://a.example.com/down", success=False, error="Could not reach URL: connection refused"
    )


def test_4xx_shape_is_unchanged_and_final_url_is_the_logical_url(wire):
    wire({("a.example.com", "/missing"): (404, None)})
    result = fetch("https://a.example.com/missing")
    assert result == FetchResult(
        url="https://a.example.com/missing",
        success=False,
        status_code=404,
        error="Server responded with HTTP 404",
        final_url="https://a.example.com/missing",
    )
    assert result.blocked is False


def test_4xx_after_a_redirect_reports_the_last_logical_url(wire):
    wire({("a.example.com", "/old"): (301, "https://b.example.com/gone"), ("b.example.com", "/gone"): (410, None)})
    result = fetch("https://a.example.com/old")
    assert result.status_code == 410
    assert result.final_url == "https://b.example.com/gone"
    assert result.blocked is False


# --- logging -----------------------------------------------------------------------------------


def test_refusal_is_logged_with_host_and_reason_but_no_secrets(wire, caplog):
    wire()
    with caplog.at_level(logging.WARNING, logger="app.tools.web_fetch"):
        fetch("http://user:hunter2@multi.example.com/path?token=SECRET123")
    text = caplog.text
    assert "multi.example.com" in text
    assert "non-public address 10.0.0.5" in text
    for secret in ("hunter2", "SECRET123", "token"):
        assert secret not in text
