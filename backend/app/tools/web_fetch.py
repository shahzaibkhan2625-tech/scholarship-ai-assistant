"""`web_fetch` tool — official-page fetch with explicit failure reporting.
A fetch failure is always reported as a fetch failure, never interpreted as
or presented as "not eligible" (US2 Acceptance Scenario 4).

010 (FR-SSRF-1..5, ADR-0016): every fetch is redirect-safe. Per hop the host is resolved, ALL its
addresses must be public, and the connection goes to ONE checked address: the IP is put in the
request URL, the original name stays in the `Host` header and (https) the `sni_hostname`
extension, so TLS still verifies the certificate against the real name while the socket can only
reach an address that passed the check. Redirects are followed here (`follow_redirects=False`
on the client), at most `settings.max_fetch_redirects` hops, http/https only, never https ->
http. `trust_env=False` so a proxy variable cannot reroute the pinned connection."""

import logging
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlparse

import httpx

from app.core.config import settings
from app.core.url_safety import UnresolvableHostError, UnsafeUrlError, _resolve_host, resolve_public

logger = logging.getLogger(__name__)

__all__ = ["BlockedFetchError", "FetchResult", "fetch_url", "safe_get"]

_TIMEOUT_SECONDS = 15.0
_USER_AGENT = "ScholarshipAIAssistant/0.1 (+https://example.invalid/bot)"
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


@dataclass(frozen=True)
class FetchResult:
    url: str
    success: bool
    status_code: int | None = None
    html: str | None = None
    error: str | None = None
    # 002: the post-redirect URL (the last URL requested, as written, never the pinned IP form), so
    # callers can tell "the page I asked for" from "a redirect landed me somewhere else".
    final_url: str | None = None
    # 010: True when the URL (or a redirect hop) was refused before any request was sent to it;
    # `error` then starts "blocked: ".
    blocked: bool = False


class BlockedFetchError(Exception):
    """A URL or redirect hop was refused before any request was sent to it. `str(exc)` is the reason."""


def _make_client(*, timeout: float, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """The one place an HTTP client is built (test seam: tests pass a recording `MockTransport`)."""
    return httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        headers={"User-Agent": _USER_AGENT},
        transport=transport,
    )


def _pin(url: httpx.URL, resolve: Callable[[str], list[str]]) -> tuple[httpx.URL, dict[str, str], dict[str, str]]:
    """(URL with the checked IP, headers, extensions) for one hop. Raises `BlockedFetchError` for a
    non-public address and `httpx.ConnectError` when the host does not resolve."""
    if url.scheme not in ("http", "https"):
        raise BlockedFetchError(f"scheme {url.scheme or '(none)'!r} is not http(s)")
    host = url.host
    if not host:
        raise BlockedFetchError("URL has no host")
    try:
        addresses = resolve_public(host, resolve=resolve)
    except UnsafeUrlError as exc:
        raise BlockedFetchError(str(exc)) from exc
    except UnresolvableHostError as exc:
        raise httpx.ConnectError(str(exc)) from exc
    # One address, IPv4 first: a fetch connects to exactly the address that was judged.
    address = sorted(addresses, key=lambda a: ":" in a)[0]
    host_is_literal = address == host.strip("[]").lower()
    ascii_host = url.raw_host.decode("ascii")
    headers = {"Host": ascii_host if url.port is None else f"{ascii_host}:{url.port}"}
    extensions: dict[str, str] = {}
    if url.scheme == "https" and not host_is_literal:
        extensions["sni_hostname"] = ascii_host
    return url.copy_with(host=address), headers, extensions


def safe_get(
    url: str,
    *,
    timeout: float = _TIMEOUT_SECONDS,
    max_redirects: int | None = None,
    params: dict[str, str] | None = None,
    resolve: Callable[[str], list[str]] | None = None,
) -> tuple[httpx.Response, str]:
    """GET `url` with the per-hop public-address check and a pinned connection.

    Returns `(response, final_url)`, `final_url` being the last URL requested as written (never the
    pinned-IP form). Raises `BlockedFetchError` (refused before any request to the offending hop),
    and `httpx.RequestError` subclasses for unresolvable hosts, timeouts and connection errors. A
    redirect response without a `Location` is returned as is. `params` apply to the first request."""
    resolve = resolve or _resolve_host
    limit = settings.max_fetch_redirects if max_redirects is None else max_redirects
    current = httpx.URL(url)
    with _make_client(timeout=timeout) as client:
        for hop in range(limit + 1):
            pinned, headers, extensions = _pin(current, resolve)
            request = client.build_request(
                "GET", pinned, params=params if hop == 0 else None, headers=headers, extensions=extensions
            )
            response = client.send(request)
            location = response.headers.get("location")
            if response.status_code not in _REDIRECT_STATUSES or not location:
                return response, str(current)
            if hop == limit:
                raise BlockedFetchError(f"too many redirects (more than {limit})")
            target = httpx.URL(urljoin(str(current), location))
            if current.scheme == "https" and target.scheme == "http":
                raise BlockedFetchError("redirect downgrades https to http")
            current = target
    raise AssertionError("unreachable")  # the loop always returns or raises


def fetch_url(
    url: str,
    *,
    timeout: float = _TIMEOUT_SECONDS,
    max_redirects: int | None = None,
    resolve: Callable[[str], list[str]] | None = None,
) -> FetchResult:
    try:
        response, final_url = safe_get(url, timeout=timeout, max_redirects=max_redirects, resolve=resolve)
    except BlockedFetchError as exc:
        # host and reason only: never the path, query, userinfo or any page content
        logger.warning("fetch refused: host=%s reason=%s", urlparse(url).hostname, exc)
        return FetchResult(url=url, success=False, blocked=True, error=f"blocked: {exc}")
    except httpx.InvalidURL as exc:
        return FetchResult(url=url, success=False, blocked=True, error=f"blocked: invalid URL ({exc})")
    except httpx.TimeoutException as exc:
        return FetchResult(url=url, success=False, error=f"Request timed out: {exc}")
    except httpx.RequestError as exc:
        return FetchResult(url=url, success=False, error=f"Could not reach URL: {exc}")

    if response.status_code >= 400:
        return FetchResult(
            url=url,
            success=False,
            status_code=response.status_code,
            error=f"Server responded with HTTP {response.status_code}",
            final_url=final_url,
        )

    return FetchResult(
        url=url,
        success=True,
        status_code=response.status_code,
        html=response.text,
        final_url=final_url,
    )
