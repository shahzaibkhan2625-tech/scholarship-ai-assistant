"""010 T332: the shared SSRF guard (moved from candidate_validation) plus `resolve_public`
and `is_public_hostname`. No network: every lookup goes through an injected resolver."""

import socket

import pytest

from app.core import url_safety
from app.core.url_safety import (
    UnresolvableHostError,
    UnsafeUrlError,
    _non_public,
    is_public_hostname,
    resolve_public,
    unsafe_url_reason,
)

PUBLIC = "93.184.216.34"


def _public(host: str) -> list[str]:
    return [PUBLIC]


def _unresolvable(host: str) -> list[str]:
    raise socket.gaierror("Name or service not known")


def _inet_aton_resolver(host: str) -> list[str]:
    """Emulates getaddrinfo's POSIX inet_aton rules for the numeric spellings of 127.0.0.1."""
    table = {"2130706433": "127.0.0.1", "0x7f.1": "127.0.0.1", "017700000001": "127.0.0.1"}
    if host in table:
        return [table[host]]
    raise socket.gaierror(host)


# --- the moved guard -----------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254", "0.0.0.0", "100.64.0.1", "::1", "fd00::1"],
)
def test_non_public_addresses(address):
    assert _non_public(address) is True


def test_public_address_and_unparseable_address():
    assert _non_public(PUBLIC) is False
    assert _non_public("not-an-ip") is True


def test_ipv4_mapped_ipv6_is_judged_as_ipv4():
    assert _non_public("::ffff:10.0.0.1") is True
    assert _non_public("::ffff:93.184.216.34") is False


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://example.com", "//example.com/x"])
def test_scheme_other_than_http_is_refused(url):
    assert unsafe_url_reason(url, resolve=_public) is not None


def test_localhost_names_are_refused_without_resolving():
    def boom(host):
        raise AssertionError("must not resolve")

    assert unsafe_url_reason("http://localhost/x", resolve=boom) is not None
    assert unsafe_url_reason("http://app.localhost/x", resolve=boom) is not None


def test_multi_address_with_one_private_is_refused():
    assert unsafe_url_reason("https://x.example.com", resolve=lambda h: [PUBLIC, "10.0.0.5"]) is not None


def test_unresolvable_host_is_not_refused_by_unsafe_url_reason():
    assert unsafe_url_reason("https://nope.example.com", resolve=_unresolvable) is None


def test_public_url_is_allowed():
    assert unsafe_url_reason("https://funds.example-uni.edu/x", resolve=_public) is None


@pytest.mark.parametrize("host", ["2130706433", "0x7f.1", "017700000001"])
def test_numeric_encodings_are_judged_on_resolved_addresses(host):
    assert unsafe_url_reason(f"http://{host}/", resolve=_inet_aton_resolver) is not None
    with pytest.raises(UnsafeUrlError):
        resolve_public(host, resolve=_inet_aton_resolver)


# --- resolve_public ------------------------------------------------------------------


def test_resolve_public_returns_the_addresses():
    assert resolve_public("example.com", resolve=_public) == [PUBLIC]


def test_resolve_public_refuses_if_any_address_is_non_public():
    with pytest.raises(UnsafeUrlError) as info:
        resolve_public("example.com", resolve=lambda h: [PUBLIC, "169.254.169.254"])
    assert "169.254.169.254" in str(info.value)


def test_resolve_public_unresolvable_is_a_distinct_error():
    with pytest.raises(UnresolvableHostError):
        resolve_public("nope.example.com", resolve=_unresolvable)
    assert not issubclass(UnresolvableHostError, UnsafeUrlError)


def test_resolve_public_ip_literal():
    assert resolve_public("93.184.216.34", resolve=_unresolvable) == [PUBLIC]
    with pytest.raises(UnsafeUrlError):
        resolve_public("10.0.0.5", resolve=_unresolvable)


# --- is_public_hostname --------------------------------------------------------------


@pytest.mark.parametrize("domain", ["10.0.0.5", "169.254.169.254", "::1", "[::1]", "8.8.8.8"])
def test_ip_literals_are_not_public_host_names(domain):
    assert is_public_hostname(domain, resolve=_public) is not None


@pytest.mark.parametrize("domain", ["intranet", "localhost", "router"])
def test_single_label_names_are_refused(domain):
    assert is_public_hostname(domain, resolve=_public) is not None


@pytest.mark.parametrize("suffix", ["alt", "example", "invalid", "local", "localhost", "onion", "test", "home.arpa"])
def test_iana_special_use_names_are_refused(suffix):
    assert is_public_hostname(f"funds.{suffix}", resolve=_public) is not None


@pytest.mark.parametrize("suffix", ["internal", "corp", "lan", "home", "intranet", "private", "localdomain"])
def test_conventional_private_suffixes_are_refused(suffix):
    assert is_public_hostname(f"funds.{suffix}", resolve=_public) is not None


def test_name_resolving_to_a_private_address_is_refused():
    assert is_public_hostname("evil.example.org", resolve=lambda h: ["10.0.0.5"]) is not None


def test_plain_public_name_is_ok():
    assert is_public_hostname("scholarships.example-uni.edu", resolve=_public) is None


def test_suffix_match_is_on_label_boundaries():
    assert is_public_hostname("notlocal.com", resolve=_public) is None
    assert is_public_hostname("labs.testing.org", resolve=_public) is None


def test_unresolvable_public_looking_name_is_ok():
    assert is_public_hostname("not-yet-live.example-uni.edu", resolve=_unresolvable) is None


def test_default_resolver_lives_in_the_shared_module():
    assert url_safety._resolve_host.__module__ == "app.core.url_safety"
