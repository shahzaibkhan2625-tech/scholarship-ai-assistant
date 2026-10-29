"""Shared outbound-URL guard (010, FR-SSRF-1/3/6).

`_resolve_host`, `_non_public` and `unsafe_url_reason` were moved UNCHANGED from
`app.services.candidate_validation` so `app.tools` can use them without importing from
`services`; that module re-exports the names. Added here: `resolve_public` (the per-hop
check the safe fetch uses) and `is_public_hostname` (the approval-time domain rule).

Numeric spellings such as `2130706433`, `0x7f.1` or `017700000001` are not parsed by
`ipaddress`, so they fall to the resolver (POSIX `inet_aton` rules) and are judged on the
addresses they RESOLVE to.
"""

import ipaddress
import socket
from typing import Callable
from urllib.parse import urlparse

__all__ = [
    "PRIVATE_NAME_SUFFIXES",
    "SPECIAL_USE_NAMES",
    "UnresolvableHostError",
    "UnsafeUrlError",
    "is_public_hostname",
    "resolve_public",
    "unsafe_url_reason",
]

# IANA special-use domain names (never publicly delegated):
# https://www.iana.org/assignments/special-use-domain-names/special-use-domain-names.xhtml
SPECIAL_USE_NAMES: tuple[str, ...] = (
    "alt",
    "example",
    "invalid",
    "local",
    "localhost",
    "onion",
    "test",
    "home.arpa",
)
# Conventional private-network suffixes (not IANA-reserved, but never public).
PRIVATE_NAME_SUFFIXES: tuple[str, ...] = (
    "internal",
    "corp",
    "lan",
    "home",
    "intranet",
    "private",
    "localdomain",
)


class UnsafeUrlError(ValueError):
    """The host (or one of the addresses it resolves to) is not a public address."""


class UnresolvableHostError(OSError):
    """The host did not resolve. Not an SSRF target: callers report it as unreachable."""


def _resolve_host(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None)
    return sorted({info[4][0].split("%")[0] for info in infos})


def _non_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True  # an address we cannot parse is not one we will connect to
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    # is_global is True for multicast (224.0.0.0/4, ff00::/8), so exclude it explicitly.
    return not ip.is_global or ip.is_multicast or ip.is_unspecified or ip.is_reserved


def unsafe_url_reason(url: str, *, resolve: Callable[[str], list[str]] = _resolve_host) -> str | None:
    """None if `url` is an http(s) URL whose host is public; otherwise why not.
    A host that does not resolve is NOT refused here (it is not an SSRF target;
    the fetch will report it as unreachable)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"scheme {parsed.scheme or '(none)'!r} is not http(s)"
    host = parsed.hostname
    if not host:
        return "URL has no host"
    host = host.lower()
    if host == "localhost" or host.endswith(".localhost"):
        return "host is localhost"
    try:
        ipaddress.ip_address(host)
    except ValueError:
        try:
            addresses = resolve(host)
        except OSError:
            return None
    else:
        addresses = [host]
    for address in addresses:
        if _non_public(address):
            return f"host resolves to non-public address {address}"
    return None


def resolve_public(host: str, *, resolve: Callable[[str], list[str]] = _resolve_host) -> list[str]:
    """The addresses `host` resolves to, all of them public.

    Raises `UnsafeUrlError` (with the reason) if any address is non-public, and the distinct
    `UnresolvableHostError` if the host does not resolve."""
    host = host.strip("[]").lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if host == "localhost" or host.endswith(".localhost"):
            raise UnsafeUrlError("host is localhost") from None
        try:
            addresses = resolve(host)
        except OSError as exc:
            raise UnresolvableHostError(f"could not resolve host: {exc}") from exc
    else:
        addresses = [host]
    for address in addresses:
        if _non_public(address):
            raise UnsafeUrlError(f"host resolves to non-public address {address}")
    return addresses


def is_public_hostname(domain: str, *, resolve: Callable[[str], list[str]] = _resolve_host) -> str | None:
    """None if `domain` is acceptable as a registry source domain; otherwise the rule it breaks.

    Static rules first (IP literal, single label, reserved/private suffix), then a lookup: a
    name that resolves to a non-public address is refused. A name that does not resolve yet is
    not refused (the monitor reports it as unreachable)."""
    name = domain.strip().strip("[]").rstrip(".").lower()
    try:
        ipaddress.ip_address(name)
    except ValueError:
        pass
    else:
        return "domain is an IP address, not a host name"
    labels = name.split(".")
    if len(labels) < 2:
        return "domain is a single-label name, not a public host name"
    for suffix in SPECIAL_USE_NAMES + PRIVATE_NAME_SUFFIXES:
        if name == suffix or name.endswith("." + suffix):
            return f"domain uses the reserved or private name '.{suffix}'"
    try:
        resolve_public(name, resolve=resolve)
    except UnsafeUrlError as exc:
        return str(exc)
    except UnresolvableHostError:
        return None
    return None
