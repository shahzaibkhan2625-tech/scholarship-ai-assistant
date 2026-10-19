"""Candidate-source pre-checks (002 US7, FR-CANDVAL-1..3, ADR-0010).

For one `pending` candidate, records ONE advisory snapshot row in
`candidate_source_validations`: reachability, extractability and heuristic
official-source signals. The result informs the human reviewer; it never
approves, rejects, promotes or otherwise changes the candidate (Principle IV):
this module's only write is `candidate_validation_repo.add`, and an AST test
bans every promotion / status-write path from it.

**Not fully LLM-free.** Extractability calls `extract_listing_from_page`, which
makes one LLM call per validated candidate (its grounding check is
deterministic; the extraction is not). That cost is why the monitor-run trigger
(T244, deferred) is capped per run.

**Ungated fetch, guarded instead.** A candidate is not a registry source, so the
source-gated connector cannot be used; the low-level `fetch_url` is. Because the
URL came from an arbitrary third-party link, `unsafe_url_reason` refuses
non-http(s) schemes and any loopback / private / link-local / reserved target,
on the requested URL and again on the post-redirect `final_url`.
**Limit:** `fetch_url` follows redirects internally, so a redirect to a
non-public address has already been requested by the time `final_url` is
checked. The check guarantees that content is discarded and recorded as
blocked, not that the request was never sent (ADR-0010).

**Transactions.** The candidate is read and the read transaction is ended
(`commit`) BEFORE any network or LLM call, so no pooled-Neon backend sits
idle-in-transaction across a 15 s fetch; the insert is a short transaction after.
"""

import ipaddress
import logging
import re
import socket
import uuid
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.data.repositories import candidate_validation_repo, source_repo
from app.models.candidate_validation import CandidateSourceValidation
from app.tools.extract_listing import ExtractListingOutput, clean_html_to_text, extract_listing_from_page
from app.tools.web_fetch import FetchResult, fetch_url

logger = logging.getLogger(__name__)

__all__ = ["AFFILIATION_TERMS", "compute_official_signals", "unsafe_url_reason", "validate_candidate"]

# Stated-affiliation text signal: matched terms only, never a score (US7 Q4).
AFFILIATION_TERMS: tuple[str, ...] = (
    "council",
    "foundation",
    "government of",
    "institute",
    "ministry",
    "university",
)
_AFFILIATION_RES = {term: re.compile(rf"\b{re.escape(term)}\b") for term in AFFILIATION_TERMS}

# `<gov|edu|ac>.<cc>` second-level patterns (e.g. .ac.uk, .gov.au, .edu.au).
_SECOND_LEVEL_OFFICIAL = frozenset({"gov", "edu", "ac"})


# --- SSRF guard -------------------------------------------------------------


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
    return not ip.is_global


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


# --- advisory official-source signals ---------------------------------------


def _domain_patterns(host: str) -> list[str]:
    patterns: list[str] = []
    labels = host.split(".")
    if labels[-1] in ("gov", "edu") and len(labels) > 1:
        patterns.append(f".{labels[-1]}")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL_OFFICIAL:
        patterns.append(f".{labels[-2]}.{labels[-1]}")
    return patterns


def compute_official_signals(url: str, html: str | None, *, matched_keyword: str | None = None) -> dict:
    """Pure heuristics, advisory only. `.org` is reported but never counted as an
    official pattern; affiliation is matched terms with no score or verdict."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    text = clean_html_to_text(html).lower() if html else ""
    return {
        "https": parsed.scheme == "https",
        "domain_patterns": _domain_patterns(host),
        "org_tld": host.endswith(".org"),
        "matched_keyword": matched_keyword,
        "affiliation_terms": [term for term, pattern in _AFFILIATION_RES.items() if pattern.search(text)],
    }


# --- the checks -------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _reachability(url: str, fetch: Callable[[str], FetchResult], resolve) -> tuple[bool, str, str | None]:
    """(reachable, detail, html). `html` is None unless the page is usable."""
    reason = unsafe_url_reason(url, resolve=resolve)
    if reason:
        return False, f"blocked: {reason}", None
    try:
        result = fetch(url)
    except Exception as exc:  # noqa: BLE001 - a fetch bug is a recorded finding, not a crash
        return False, f"fetch raised {type(exc).__name__}: {exc}", None
    if not result.success:
        return False, result.error or f"HTTP {result.status_code}", None
    final_url = result.final_url or url
    if final_url != url:
        redirect_reason = unsafe_url_reason(final_url, resolve=resolve)
        if redirect_reason:
            return False, f"blocked: redirect target {final_url} is not allowed ({redirect_reason})", None
    detail = f"HTTP {result.status_code}"
    if final_url != url:
        detail += f" (redirected to {final_url})"
    return True, detail, result.html or ""


def _extractability(
    html: str, source_url: str, extract_listing: Callable[..., ExtractListingOutput]
) -> tuple[bool, str]:
    try:
        output = extract_listing(html, source_url=source_url)
    except Exception as exc:  # noqa: BLE001
        return False, f"extraction raised {type(exc).__name__}: {exc}"
    detail = f"{output.accepted_count} grounded listing(s) extracted"
    if output.rejected_count:
        detail += f", {output.rejected_count} rejected as ungrounded"
    return output.accepted_count > 0, detail


def validate_candidate(
    db: Session,
    candidate_id: uuid.UUID,
    *,
    fetch: Callable[[str], FetchResult] = fetch_url,
    extract_listing: Callable[..., ExtractListingOutput] = extract_listing_from_page,
    resolve: Callable[[str], list[str]] = _resolve_host,
    now: Callable[[], datetime] | None = None,
) -> CandidateSourceValidation:
    """Run the three checks against one candidate and append one result row.
    Raises `CandidateSourceNotFoundError` for an unknown id. Fetch/extract
    failures are recorded in the row, not raised."""
    candidate = source_repo.get_candidate_source_by_id(db, candidate_id)
    if candidate is None:
        raise source_repo.CandidateSourceNotFoundError(f"candidate_source {candidate_id} not found")
    url = candidate.url
    matched_keyword = (candidate.signals or {}).get("matched_keyword")
    db.commit()  # end the read transaction before any network / LLM call

    reachable, reachable_detail, html = _reachability(url, fetch, resolve)
    if html is None:
        extractable, extractable_detail = False, "not attempted: page not reachable"
    else:
        extractable, extractable_detail = _extractability(html, url, extract_listing)

    return candidate_validation_repo.add(
        db,
        candidate_id,
        reachable=reachable,
        reachable_detail=reachable_detail,
        extractable=extractable,
        extractable_detail=extractable_detail,
        official_signals=compute_official_signals(url, html, matched_keyword=matched_keyword),
        checked_at=(now or _utcnow)(),
    )
