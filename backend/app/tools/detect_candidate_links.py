"""`detect_candidate_links` tool (US4 Acceptance Scenario 3, ADR-0005) — pure,
deterministic extraction of "unrecognized domain, worth flagging as a
candidate source" signals from an already-fetched listing page's RAW HTML.

This runs on raw HTML, not the tag-stripped text `extract_listing.py` feeds
to its LLM call: `clean_html_to_text` there strips every `<a href>`
attribute along with the tags, so link detection has to happen upstream of
that, directly on `fetch_result.content` (see the connector-tier wiring in
`app/sources/connectors/official_fetch.py`, Slice 3).

No LLM judgment call is involved anywhere in this module (constitution
Principle IV: the system never asks a model "does this link look like a
scholarship source"). A link is flagged only if it passes ALL of:
  1. its domain differs from the fetched page's own domain (same-site nav/
     footer links are the overwhelming majority of links on any page);
  2. its domain is not already known to the registry in ANY status — the
     caller passes `known_domains` (built from `source_repo.get_source_by_domain`/
     `get_all_sources`, a READ, never a fetch gate);
  3. its domain is not on `_DENYLIST_DOMAINS` (social/share buttons, cookie-
     consent vendors, generic ad/analytics infra — exactly the noise ADR-0005's
     honest false-positive estimate is about);
  4. its link text or URL path contains one of `_KEYWORDS` — a fixed,
     hand-maintained scholarship-related term list, not a model's guess.

`MAX_CANDIDATE_LINKS_PER_FETCH` bounds one call's output to at most 5
unique-domain signals, taken in document order. A page with more qualifying
links than that (a link farm, a "sponsored" section) silently loses the
rest for that call — this is a best-effort discovery bound, not a
completeness guarantee, and is therefore never surfaced as a coverage gap
(ADR-0005 Decision 4).
"""

from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

__all__ = ["CandidateLinkSignal", "MAX_CANDIDATE_LINKS_PER_FETCH", "find_candidate_links"]

MAX_CANDIDATE_LINKS_PER_FETCH = 5

# Social/share buttons, cookie-consent vendors, and generic ad/analytics/CDN
# infra that shows up as outbound links on almost every real listing page —
# never a scholarship source, so never worth a reviewer's time.
_DENYLIST_DOMAINS: frozenset[str] = frozenset(
    {
        "facebook.com",
        "twitter.com",
        "x.com",
        "linkedin.com",
        "instagram.com",
        "youtube.com",
        "youtu.be",
        "whatsapp.com",
        "t.me",
        "pinterest.com",
        "google.com",
        "accounts.google.com",
        "maps.google.com",
        "googletagmanager.com",
        "google-analytics.com",
        "googleapis.com",
        "gstatic.com",
        "doubleclick.net",
        "onetrust.com",
        "cookiebot.com",
        "cookielaw.org",
        "addthis.com",
        "sharethis.com",
    }
)

# A link is only flagged if its text or URL path contains one of these —
# fixed, hand-maintained, case-insensitive substring match (ADR-0005
# Decision 2: deterministic filter, never an LLM judgment call).
_KEYWORDS: tuple[str, ...] = (
    "scholarship",
    "fellowship",
    "bursary",
    "stipend",
    "studentship",
    "grant",
    "funding",
)


@dataclass(frozen=True)
class CandidateLinkSignal:
    domain: str
    url: str
    matched_keyword: str


def _normalize_domain(netloc: str) -> str:
    host = netloc.lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[len("www."):]
    return host


def _matched_keyword(haystacks: list[str]) -> str | None:
    combined = " ".join(haystacks).lower()
    for keyword in _KEYWORDS:
        if keyword in combined:
            return keyword
    return None


def find_candidate_links(
    html: str,
    *,
    source_url: str,
    known_domains: set[str],
    max_candidates: int = MAX_CANDIDATE_LINKS_PER_FETCH,
) -> list[CandidateLinkSignal]:
    """Document-order scan of every `<a href>` in `html`; returns at most
    `max_candidates` signals, one per unique unrecognized-and-qualifying
    domain. `known_domains` must already be normalized the same way this
    function normalizes (`_normalize_domain`) — the caller owns building
    that set from the registry."""

    own_domain = _normalize_domain(urlparse(source_url).netloc)
    normalized_known = {d.lower() for d in known_domains}

    soup = BeautifulSoup(html or "", "html.parser")

    signals: list[CandidateLinkSignal] = []
    seen_domains: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        if len(signals) >= max_candidates:
            break

        href = anchor["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue

        absolute_url = urljoin(source_url, href)
        parsed = urlparse(absolute_url)
        if parsed.scheme not in ("http", "https"):
            continue

        domain = _normalize_domain(parsed.netloc)
        if not domain or domain == own_domain:
            continue
        if domain in seen_domains:
            continue
        if domain in normalized_known:
            continue
        if any(domain == denied or domain.endswith(f".{denied}") for denied in _DENYLIST_DOMAINS):
            continue

        link_text = anchor.get_text(separator=" ", strip=True)
        keyword = _matched_keyword([link_text, parsed.path])
        if keyword is None:
            continue

        seen_domains.add(domain)
        signals.append(CandidateLinkSignal(domain=domain, url=absolute_url, matched_keyword=keyword))

    return signals
