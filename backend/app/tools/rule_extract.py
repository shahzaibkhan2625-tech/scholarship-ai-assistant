"""Rule-based listing extraction (003 US5, FR-RULES-1/2/4, research R-11): a declarative engine over
`beautifulsoup4` (already a dependency) that returns the same output shape as `extract_listing_from_page`, with
no LLM request.

Every name passes the same grounding gate as an LLM result (`ground_check`, threshold 1.0), compared against
the text as the HTML PARSER decodes it (`&amp;` becomes `&`), because `clean_html_to_text` does not unescape
entities. Nothing but the name and the detail link is read from the page; provider comes from the registry
`organization` only when the rule asks for it and it is set. Country, field, degree and funding stay unknown
(constitution Principle I: nothing is guessed). Pure: no database, no network.
"""

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.core.config import settings
from app.schemas.extraction_rules import DeterministicRules
from app.tools.extract_listing import ExtractedListingCandidate, ExtractListingOutput
from app.tools.ground_check import ground_check

__all__ = ["RuleExtraction", "decoded_text", "normalise_name", "rule_extract"]

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class RuleExtraction:
    output: ExtractListingOutput  # grounded candidates only; ungrounded names are `rejected_names`
    raw_count: int  # items the selector matched (before de-duplication)
    unique_count: int  # unique names read (before grounding)
    matched: bool  # grounded unique count >= `min_items`: the rules found the listing
    anomaly: str | None  # "below_low_ratio" | "above_high_ratio" against the previous extraction, else None


def normalise_name(name: str) -> str:
    return _WHITESPACE_RE.sub(" ", name).strip().casefold()


def decoded_text(html: str) -> str:
    """Visible text as the parser decodes it (entities resolved), whitespace collapsed."""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return _WHITESPACE_RE.sub(" ", soup.get_text(" ")).strip()


def _read_name(item, rules: DeterministicRules) -> str | None:
    if rules.name.attribute is not None:
        value = item.get(rules.name.attribute)
        value = value if isinstance(value, str) else None
    else:
        target = item.select_one(rules.name.selector)
        value = target.get_text(" ") if target is not None else None
    cleaned = _WHITESPACE_RE.sub(" ", value).strip() if value else ""
    return cleaned or None


def _read_url(item, rules: DeterministicRules, source_url: str) -> str | None:
    if rules.detail_url is None:
        return None
    target = item.select_one(rules.detail_url.selector) if rules.detail_url.selector else item
    href = target.get(rules.detail_url.attribute) if target is not None else None
    if not isinstance(href, str) or not href.strip():
        return None
    absolute = urljoin(source_url, href.strip())
    parsed, base = urlparse(absolute), urlparse(source_url)
    if parsed.scheme not in ("http", "https") or (parsed.hostname or "").lower() != (base.hostname or "").lower():
        return None  # restricted to the source's own domain; the item itself is kept
    return absolute


def _anomaly(count: int, previous: int | None, rules: DeterministicRules) -> str | None:
    if not previous:
        return None
    low = rules.anomaly_low_ratio if rules.anomaly_low_ratio is not None else settings.rules_anomaly_low_ratio
    high = rules.anomaly_high_ratio if rules.anomaly_high_ratio is not None else settings.rules_anomaly_high_ratio
    if count < low * previous:
        return "below_low_ratio"
    if count > high * previous:
        return "above_high_ratio"
    return None


def rule_extract(
    html: str,
    rules: DeterministicRules,
    *,
    source_url: str,
    organization: str | None = None,
    previous_count: int | None = None,
) -> RuleExtraction:
    soup = BeautifulSoup(html or "", "html.parser")
    items = soup.select(rules.item_selector)
    page_text = decoded_text(html)
    provider = organization if (rules.provider_from_registry and organization) else None

    seen: set[str] = set()
    accepted: list[ExtractedListingCandidate] = []
    rejected: list[str] = []
    for item in items:
        name = _read_name(item, rules)
        if name is None:
            continue
        key = normalise_name(name)
        if key in seen:
            continue  # order-preserving de-duplication
        seen.add(key)
        if not ground_check(name, [page_text], threshold=1.0).grounded:
            rejected.append(name)
            continue
        accepted.append(
            ExtractedListingCandidate(name=name, provider=provider, detail_url=_read_url(item, rules, source_url))
        )
    output = ExtractListingOutput(
        candidates=accepted, accepted_count=len(accepted), rejected_count=len(rejected), rejected_names=rejected
    )
    return RuleExtraction(
        output=output,
        raw_count=len(items),
        unique_count=len(seen),
        matched=len(accepted) >= rules.min_items,
        anomaly=_anomaly(len(accepted), previous_count, rules),
    )
