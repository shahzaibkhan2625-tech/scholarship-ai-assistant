"""Service tests: candidate-source pre-checks (002 US7 T238, FR-CANDVAL-1..3,
Edge Case "candidate characteristics change"). The fetch / extract / DNS seams
are injected, so nothing here touches the network or an LLM. DB-backed tests run
inside the rolled-back `db_session_factory` transaction."""

import ast
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.data.repositories import candidate_validation_repo, source_repo
from app.models.source import CandidateSourceStatus, SourceRegistry
from app.schemas.source import CandidateSourceCreate
from app.services import candidate_validation as cv
from app.tools.extract_listing import ExtractListingOutput
from app.tools.web_fetch import FetchResult

_PUBLIC_IP = "93.184.216.34"
_T0 = datetime(2027, 3, 1, 12, 0, tzinfo=timezone.utc)
_PAGE = "<html><body><h1>Scholarships</h1><p>The University Foundation offers awards.</p></body></html>"


def _public(host: str) -> list[str]:
    return [_PUBLIC_IP]


def _ok_fetch(html: str = _PAGE, final_url: str | None = None):
    calls: list[str] = []

    def fetch(url: str) -> FetchResult:
        calls.append(url)
        return FetchResult(url=url, success=True, status_code=200, html=html, final_url=final_url or url)

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def _extract(accepted: int):
    calls: list[str] = []

    def extract(html: str, *, source_url: str) -> ExtractListingOutput:
        calls.append(source_url)
        return ExtractListingOutput(candidates=[], accepted_count=accepted, rejected_count=0)

    extract.calls = calls  # type: ignore[attr-defined]
    return extract


@pytest.fixture
def session(db_session_factory):
    s = db_session_factory()
    yield s
    s.close()


def _candidate(session, url="https://funds.example-uni.edu/scholarships", **signals):
    return source_repo.create_candidate_source(session, CandidateSourceCreate(url=url, signals=signals))


# --- official-signal heuristics (pure) --------------------------------------


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://study.example.gov/awards", True),
        ("https://www.example.edu/awards", True),
        ("https://www.example.ac.uk/awards", True),
        ("https://www.example.gov.au/awards", True),
        ("https://www.example.com/awards", False),
        ("https://www.example.org/awards", False),  # .org is reported, never counted as official
    ],
)
def test_domain_pattern_signal(url, expected):
    signals = cv.compute_official_signals(url, None)
    assert bool(signals["domain_patterns"]) is expected


def test_org_tld_and_https_are_reported_separately():
    signals = cv.compute_official_signals("http://example.org/x", None)
    assert signals["org_tld"] is True
    assert signals["https"] is False
    assert cv.compute_official_signals("https://example.com", None)["https"] is True


def test_affiliation_terms_are_matched_terms_only_with_no_score():
    html = "<p>The Ministry of Education and the National Council fund the Institute.</p>"
    signals = cv.compute_official_signals("https://example.com", html)
    assert signals["affiliation_terms"] == ["council", "institute", "ministry"]
    assert not any(key in signals for key in ("score", "verdict", "official", "passed"))


def test_affiliation_terms_match_whole_words_and_phrases():
    signals = cv.compute_official_signals("https://example.com", "<p>Government of Atlantis; universality; foundations</p>")
    assert signals["affiliation_terms"] == ["government of"]


def test_no_html_means_no_affiliation_terms_and_keyword_is_passed_through():
    signals = cv.compute_official_signals("https://example.com", None, matched_keyword="grant")
    assert signals["affiliation_terms"] == []
    assert signals["matched_keyword"] == "grant"


# --- SSRF guard -------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/x",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://localhost/x",
        "http://127.0.0.1:8000/x",
        "http://10.1.2.3/x",
        "http://172.16.0.9/x",
        "http://192.168.1.1/x",
        "http://169.254.169.254/latest/meta-data",
        "http://0.0.0.0/x",
        "http://[::1]/x",
        "http://[fe80::1]/x",
        "http:///nohost",
    ],
)
def test_unsafe_urls_are_refused(url):
    assert cv.unsafe_url_reason(url, resolve=_public) is not None


def test_public_https_url_is_allowed():
    assert cv.unsafe_url_reason("https://funds.example-uni.edu/x", resolve=_public) is None


def test_hostname_resolving_to_a_private_address_is_refused():
    assert cv.unsafe_url_reason("https://evil.example.com/x", resolve=lambda h: ["10.0.0.5"]) is not None


def test_any_private_address_among_several_is_refused():
    assert cv.unsafe_url_reason("https://x.example.com", resolve=lambda h: [_PUBLIC_IP, "127.0.0.1"]) is not None


def test_unresolvable_host_is_not_ssrf_it_is_left_to_the_fetch_to_report():
    def boom(host):
        raise OSError("no such host")

    assert cv.unsafe_url_reason("https://nope.example.com", resolve=boom) is None


def test_blocked_requested_url_is_never_fetched(session):
    candidate = _candidate(session, url="http://169.254.169.254/latest/meta-data")
    fetch, extract = _ok_fetch(), _extract(3)
    row = cv.validate_candidate(session, candidate.id, fetch=fetch, extract_listing=extract, resolve=_public, now=lambda: _T0)
    assert fetch.calls == [] and extract.calls == []
    assert row.reachable is False and row.extractable is False
    assert "blocked" in row.reachable_detail


def test_redirect_to_a_private_address_is_discarded_and_recorded_as_blocked(session):
    candidate = _candidate(session)
    fetch = _ok_fetch(final_url="http://10.0.0.8/admin")
    extract = _extract(3)
    row = cv.validate_candidate(session, candidate.id, fetch=fetch, extract_listing=extract, resolve=_public, now=lambda: _T0)
    assert fetch.calls == [candidate.url]
    assert extract.calls == []  # content from the redirect target is never used
    assert row.reachable is False and "blocked" in row.reachable_detail and "redirect" in row.reachable_detail
    assert row.official_signals["affiliation_terms"] == []  # nor mined for signals


# --- the three checks -------------------------------------------------------


def test_reachability_uses_the_ungated_fetch_on_the_candidate_url(session):
    candidate = _candidate(session)
    fetch = _ok_fetch()
    row = cv.validate_candidate(session, candidate.id, fetch=fetch, extract_listing=_extract(2), resolve=_public, now=lambda: _T0)
    assert fetch.calls == [candidate.url]
    assert row.reachable is True and "200" in row.reachable_detail


def test_unreachable_candidate_records_the_error_and_skips_extraction(session):
    candidate = _candidate(session)
    extract = _extract(5)

    def fetch(url):
        return FetchResult(url=url, success=False, status_code=503, error="Server responded with HTTP 503")

    row = cv.validate_candidate(session, candidate.id, fetch=fetch, extract_listing=extract, resolve=_public, now=lambda: _T0)
    assert row.reachable is False and "503" in row.reachable_detail
    assert row.extractable is False and "not attempted" in row.extractable_detail
    assert extract.calls == []


def test_extractable_requires_at_least_one_accepted_listing(session):
    candidate = _candidate(session)
    yes = cv.validate_candidate(session, candidate.id, fetch=_ok_fetch(), extract_listing=_extract(2), resolve=_public, now=lambda: _T0)
    no = cv.validate_candidate(session, candidate.id, fetch=_ok_fetch(), extract_listing=_extract(0), resolve=_public, now=lambda: _T0 + timedelta(minutes=1))
    assert yes.extractable is True
    assert no.extractable is False and "0" in no.extractable_detail


def test_fetch_or_extract_exceptions_are_recorded_not_raised(session):
    candidate = _candidate(session)

    def bad_fetch(url):
        raise RuntimeError("socket exploded")

    row = cv.validate_candidate(session, candidate.id, fetch=bad_fetch, extract_listing=_extract(1), resolve=_public, now=lambda: _T0)
    assert row.reachable is False and "socket exploded" in row.reachable_detail

    def bad_extract(html, *, source_url):
        raise ValueError("llm down")

    row = cv.validate_candidate(session, candidate.id, fetch=_ok_fetch(), extract_listing=bad_extract, resolve=_public, now=lambda: _T0 + timedelta(minutes=1))
    assert row.reachable is True and row.extractable is False and "llm down" in row.extractable_detail


def test_signals_are_stored_as_advisory_json_and_never_gate_the_checks(session):
    candidate = _candidate(session, url="https://awards.example.gov/x", matched_keyword="scholarship")
    row = cv.validate_candidate(session, candidate.id, fetch=_ok_fetch(), extract_listing=_extract(0), resolve=_public, now=lambda: _T0)
    assert row.official_signals["domain_patterns"] == [".gov"]
    assert row.official_signals["matched_keyword"] == "scholarship"
    assert row.official_signals["affiliation_terms"] == ["foundation", "university"]
    assert row.extractable is False  # strong official signals do not rescue a failed check


def test_unknown_candidate_raises_not_found(session):
    with pytest.raises(source_repo.CandidateSourceNotFoundError):
        cv.validate_candidate(session, uuid.uuid4(), fetch=_ok_fetch(), extract_listing=_extract(1), resolve=_public)


# --- append-only history ----------------------------------------------------


def test_rerun_appends_and_latest_by_checked_at_is_current(session):
    candidate = _candidate(session)
    first = cv.validate_candidate(session, candidate.id, fetch=_ok_fetch(), extract_listing=_extract(0), resolve=_public, now=lambda: _T0)
    second = cv.validate_candidate(session, candidate.id, fetch=_ok_fetch(), extract_listing=_extract(4), resolve=_public, now=lambda: _T0 + timedelta(days=8))
    history = candidate_validation_repo.list_for_candidate(session, candidate.id)
    assert [row.id for row in history] == [second.id, first.id]
    assert candidate_validation_repo.latest_for_candidate(session, candidate.id).id == second.id
    assert first.extractable is False and second.extractable is True  # the old row was not overwritten


def test_repo_orders_by_explicit_checked_at_not_insertion_order(session):
    candidate = _candidate(session)
    newer = candidate_validation_repo.add(
        session, candidate.id, reachable=True, extractable=True, official_signals={}, checked_at=_T0 + timedelta(hours=1)
    )
    older = candidate_validation_repo.add(
        session, candidate.id, reachable=False, extractable=False, official_signals={}, checked_at=_T0
    )
    assert [r.id for r in candidate_validation_repo.list_for_candidate(session, candidate.id)] == [newer.id, older.id]
    assert candidate_validation_repo.latest_for_candidate(session, candidate.id).id == newer.id


def test_latest_for_candidate_without_history_is_none(session):
    assert candidate_validation_repo.latest_for_candidate(session, _candidate(session).id) is None


# --- governance invariants --------------------------------------------------


def test_validation_never_changes_candidate_status_or_creates_a_registry_row(session):
    candidate = _candidate(session, url="https://awards.example.gov/x")
    before = session.query(SourceRegistry).count()
    cv.validate_candidate(session, candidate.id, fetch=_ok_fetch(), extract_listing=_extract(9), resolve=_public, now=lambda: _T0)
    session.refresh(candidate)
    assert candidate.status == CandidateSourceStatus.PENDING
    assert candidate.reviewed_by is None and candidate.reviewed_at is None
    assert session.query(SourceRegistry).count() == before
    assert source_repo.get_source_by_domain(session, "awards.example.gov") is None


_FORBIDDEN_NAMES = {
    "approve_candidate_source",
    "reject_candidate_source",
    "upsert_source_by_domain",
    "create_candidate_source",
    "record_or_bump_candidate_source",
    "SourceRegistry",
}
_SERVICE = Path(cv.__file__)


def test_service_module_has_no_promotion_or_status_write_path():
    tree = ast.parse(_SERVICE.read_text(encoding="utf-8"))
    names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    names |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
    assert not (names & _FORBIDDEN_NAMES), f"forbidden names in candidate_validation: {names & _FORBIDDEN_NAMES}"

    status_writes = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign))
        and any(isinstance(t, ast.Attribute) and t.attr in {"status", "reviewed_by", "reviewed_at"}
                for t in (node.targets if isinstance(node, ast.Assign) else [node.target]))
    ]
    assert status_writes == []


def test_service_does_not_use_the_source_gated_official_fetch_connector():
    source = _SERVICE.read_text(encoding="utf-8")
    assert "official_fetch" not in source
    imports = [
        (node.module, alias.name)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    ]
    assert ("app.tools.web_fetch", "fetch_url") in imports
