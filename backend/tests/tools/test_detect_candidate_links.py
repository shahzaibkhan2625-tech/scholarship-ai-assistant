"""`detect_candidate_links` tool tests (US4 Acceptance Scenario 3, ADR-0005).
Pure function, no DB/network — fixture HTML snippets below are hand-built
to exercise each filter independently (same-domain, denylist, keyword-match,
cap), not captures of a real page.

`test_eacea_shaped_page_flags_only_the_genuinely_external_links` is the one
exception worth calling out: reconstructed (not byte-for-byte, per this
project's "no external test-data dependency" convention — see
`test_extract_listing.py`'s module docstring) from markup shapes observed
by live-fetching the real EACEA seeded source's listing page during Slice 4
of this feature, as a one-time sanity check of the denylist/keyword filter
against real-world structure — zero LLM involved, so it needed zero quota,
and this committed version needs none either."""

from app.tools.detect_candidate_links import (
    MAX_CANDIDATE_LINKS_PER_FETCH,
    find_candidate_links,
)

_SOURCE_URL = "https://www2.daad.de/en/scholarships/"

_MIXED_HTML = """
<html><body>
<nav>
  <a href="/en/about">About</a>
  <a href="https://www.facebook.com/daad">Follow us</a>
  <a href="https://twitter.com/daad">Tweet</a>
  <a href="https://onetrust.com/cookie-policy">Cookie Policy</a>
</nav>
<main>
  <a href="https://new-scholarship-fund.example.org/apply">New Scholarship Fund</a>
  <a href="https://already-known.example.com/scholarships">Already Known Provider</a>
  <a href="https://random-blog.example.net/travel-tips">Unrelated travel blog</a>
</main>
</body></html>
"""


def test_same_domain_links_are_never_flagged():
    html = '<html><body><a href="https://www2.daad.de/en/scholarships/list">Scholarship List</a></body></html>'
    signals = find_candidate_links(html, source_url=_SOURCE_URL, known_domains=set())
    assert signals == []


def test_denylisted_social_and_infra_domains_are_never_flagged():
    html = """
    <a href="https://www.facebook.com/scholarship-page">Scholarship Facebook</a>
    <a href="https://twitter.com/scholarship-fund">Scholarship Tweet</a>
    <a href="https://onetrust.com/scholarship-cookies">Scholarship Cookies</a>
    """
    signals = find_candidate_links(html, source_url=_SOURCE_URL, known_domains=set())
    assert signals == []


def test_already_known_domain_is_never_flagged_regardless_of_keyword_match():
    html = '<html><body><a href="https://already-known.example.com/scholarships">More scholarships here</a></body></html>'
    signals = find_candidate_links(html, source_url=_SOURCE_URL, known_domains={"already-known.example.com"})
    assert signals == []


def test_link_without_a_scholarship_keyword_is_not_flagged():
    html = '<html><body><a href="https://random-blog.example.net/travel-tips">Unrelated travel blog</a></body></html>'
    signals = find_candidate_links(html, source_url=_SOURCE_URL, known_domains=set())
    assert signals == []


def test_unrecognized_keyword_matched_domain_is_flagged():
    html = '<html><body><a href="https://new-scholarship-fund.example.org/apply">New Scholarship Fund</a></body></html>'
    signals = find_candidate_links(html, source_url=_SOURCE_URL, known_domains=set())

    assert len(signals) == 1
    assert signals[0].domain == "new-scholarship-fund.example.org"
    assert signals[0].url == "https://new-scholarship-fund.example.org/apply"
    assert signals[0].matched_keyword == "scholarship"


def test_mixed_page_flags_only_the_one_qualifying_unrecognized_domain():
    signals = find_candidate_links(
        _MIXED_HTML, source_url=_SOURCE_URL, known_domains={"already-known.example.com"}
    )

    assert len(signals) == 1
    assert signals[0].domain == "new-scholarship-fund.example.org"


def test_same_domain_repeated_across_multiple_links_counts_once():
    html = """
    <a href="https://new-fund.example.org/scholarships/a">Scholarship A</a>
    <a href="https://new-fund.example.org/scholarships/b">Scholarship B</a>
    """
    signals = find_candidate_links(html, source_url=_SOURCE_URL, known_domains=set())

    assert len(signals) == 1
    assert signals[0].domain == "new-fund.example.org"


def test_www_prefix_is_normalized_for_domain_comparison():
    html = '<html><body><a href="https://www.new-scholarship-fund.example.org/apply">Scholarship Fund</a></body></html>'
    signals = find_candidate_links(
        html, source_url=_SOURCE_URL, known_domains={"new-scholarship-fund.example.org"}
    )
    # already known (modulo www.) -> not flagged
    assert signals == []


def test_relative_and_non_http_links_are_ignored():
    html = """
    <a href="#scholarship-section">Jump to scholarships</a>
    <a href="mailto:scholarships@example.org">Email scholarships</a>
    <a href="javascript:void(0)">Scholarship popup</a>
    <a href="/en/scholarships">Internal scholarships link</a>
    """
    signals = find_candidate_links(html, source_url=_SOURCE_URL, known_domains=set())
    assert signals == []


def test_cap_truncates_to_max_candidates_in_document_order():
    links = "".join(
        f'<a href="https://scholarship-site-{i}.example.org/apply">Scholarship Site {i}</a>'
        for i in range(MAX_CANDIDATE_LINKS_PER_FETCH + 3)
    )
    signals = find_candidate_links(f"<html><body>{links}</body></html>", source_url=_SOURCE_URL, known_domains=set())

    assert len(signals) == MAX_CANDIDATE_LINKS_PER_FETCH
    assert [s.domain for s in signals] == [
        f"scholarship-site-{i}.example.org" for i in range(MAX_CANDIDATE_LINKS_PER_FETCH)
    ]


def test_empty_html_returns_no_signals():
    assert find_candidate_links("", source_url=_SOURCE_URL, known_domains=set()) == []


# Reconstructed (not byte-for-byte) from the real EACEA listing page's
# markup shapes: an inline prose "read more" link to a related EU
# scholarship portal, a social-share footer link, a cookie-consent footer
# link, and a generic EU funding-portal footer link whose URL *path*
# contains a keyword even though its visible text doesn't.
_EACEA_SHAPED_HTML = """
<html><body>
<main>
  <p>Master's level students from all over the world can apply. Read more to find out
  if an <a href="https://erasmus-plus.ec.europa.eu/opportunities/individuals/students/erasmus-mundus-joint-masters-scholarships">
  <u>Erasmus Mundus Joint Master</u></a> is the right fit.</p>
</main>
<footer class="ecl-site-footer">
  <ul class="ecl-site-footer__list">
    <li><a href="https://www.facebook.com/EACEA" class="ecl-link ecl-link--standalone">Follow us on Facebook</a></li>
    <li><a href="https://onetrust.com/cookie-policy" class="ecl-link ecl-link--standalone">Cookie Policy</a></li>
    <li><a href="https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/home"
           class="ecl-link ecl-link--standalone ecl-link--inverted ecl-site-footer__link">EACEA Department Page</a></li>
  </ul>
</footer>
</body></html>
"""


def test_eacea_shaped_page_flags_only_the_genuinely_external_links():
    signals = find_candidate_links(
        _EACEA_SHAPED_HTML,
        source_url="https://www.eacea.ec.europa.eu/scholarships/emjmd-catalogue_en",
        known_domains={"www.eacea.ec.europa.eu"},
    )

    domains = {s.domain for s in signals}
    # Social-share and cookie-consent footer chrome never flagged.
    assert "facebook.com" not in domains
    assert "onetrust.com" not in domains
    # A genuinely related, keyword-matched scholarship portal IS flagged...
    assert "erasmus-plus.ec.europa.eu" in domains
    # ...and a generic EU funding portal is flagged via its URL *path*
    # keyword even though the link text itself doesn't name a scholarship —
    # an honest example of this filter's real-world false-positive rate
    # (ADR-0005 Decision 4/A6): not junk, but not a scholarship-specific
    # source either, left for the human reviewer to judge.
    assert "ec.europa.eu" in domains
    assert len(signals) == 2
