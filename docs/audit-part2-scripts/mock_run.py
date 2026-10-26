# Audit Part 2 helper (read-only, not application code).
# Run from the backend folder, never from here:
#   cd backend && PYTHONPATH=. .venv/Scripts/python ../docs/audit-part2-scripts/mock_run.py     (Windows)
#   cd backend && PYTHONPATH=. .venv/bin/python ../docs/audit-part2-scripts/mock_run.py          (Linux/macOS)
# It mocks the LLM, HTTP, Qdrant and the database, and makes any socket connection raise.
"""Read-only verification of Part 1 by-reading items. LLM, HTTP, Qdrant, DB all mocked.
Network is hard-blocked; any socket use raises."""
import socket
import uuid
from types import SimpleNamespace as NS
from unittest import mock


def _blocked(*a, **k):
    raise AssertionError("NETWORK ATTEMPT BLOCKED")


socket.socket.connect = _blocked
socket.create_connection = _blocked
socket.getaddrinfo = _blocked

print("=== T-05: LLM calls per classified candidate (generate_text mocked) ===")
import app.core.llm as llm

calls = []


def fake_generate(prompt, system_instruction=None, **k):
    calls.append(prompt[:60])
    return "{}"


with mock.patch.object(llm, "generate_text", fake_generate):
    from app.services.classify import classify_candidate

    samples = {
        "name only + funding unknown": {"name": "Erasmus Mundus Joint Master in Marine Biology", "funding_status": "unknown"},
        "all listing fields filled": {"name": "Chevening Scholarship", "provider": "UK Government", "country": "United Kingdom", "field": "Engineering", "degree_level": "MS", "funding_status": "fully_funded"},
        "country+field+degree+funding, no provider": {"name": "X Scholars Programme", "country": "Germany", "field": "Computer Science", "degree_level": "MS", "funding_status": "fully_funded"},
    }
    for label, raw in samples.items():
        calls.clear()
        text = " ".join(str(v) for v in raw.values() if v)  # same join as ingestion/graph.py:172
        r = classify_candidate(text)
        print(f"  {label}: LLM calls={len(calls)}; rules resolved: degree={r.degree_level} funding={r.funding_status} provider={r.provider_type} country={r.country} field={r.field}")

print("\n=== S-10 / MA-03 / A-05e: hard constraints and verdict ===")
from datetime import date, timedelta

from app.agents.matching.agent import build_verdict
from app.models.profile import CriterionKind
from app.models.requirement import Requirement, RequirementCategory
from app.models.scholarship import Scholarship
from app.schemas.match import CriterionOutcome, CriterionResult
from app.services.hard_constraints import evaluate_hard_constraints


def profile(dim=None, gpa=3.5):
    crit = [NS(kind=CriterionKind.HARD_CONSTRAINT, dimension=dim)] if dim else []
    return NS(criteria=crit, education_records=[NS(gpa=gpa)], nationality="Pakistan", target_degree_level=None, test_scores=[])


def sch(deadline=None, gpa_value=None):
    s = Scholarship(name="S", deadline=deadline)
    if gpa_value is not None:
        s.requirements = [Requirement(category=RequirementCategory.GPA, key="gpa", value=gpa_value, mandatory=True, value_status="known", confidence="verified")]
    return s


try:
    evaluate_hard_constraints(profile("gpa"), sch(date.today() + timedelta(days=30), "3.0 or equivalent"))
    print("  GPA text value: no exception")
except Exception as e:
    print(f"  GPA requirement value '3.0 or equivalent' -> {type(e).__name__}: {e}")
out = evaluate_hard_constraints(profile(None), sch(None))
print("  no rules + no deadline ->", [(o.criterion, o.result.value) for o in out], "-> verdict", build_verdict(uuid.uuid4(), out, [], [], []).eligibility_verdict.value)
out = evaluate_hard_constraints(profile(None), sch(date.today() + timedelta(days=30)))
print("  no rules + future deadline ->", [(o.criterion, o.result.value) for o in out], "-> verdict", build_verdict(uuid.uuid4(), out, [], [], []).eligibility_verdict.value)
v = build_verdict(uuid.uuid4(), out, [], ["requires GRE"], [])
print("  same, exclusions_triggered=['requires GRE'] ->", v.eligibility_verdict.value, "/", v.match_strength.value, "| echoed:", v.exclusions_triggered)

print("\n=== W-01b: url_match on an already-stored URL ===")
import app.workflows.url_match.graph as um

cnt = {"extract": 0, "ingest": 0, "create": 0}
existing = NS(id=uuid.uuid4())


def fake_fetch(url):
    return NS(success=True, html="<html><body><p>Scholarship text about GPA and deadlines.</p></body></html>", error=None)


def fake_extract(text, source_url):
    cnt["extract"] += 1
    return NS(name="n")


def fake_ingest(html, source_url, scholarship_id):
    cnt["ingest"] += 1
    return 1


prof = profile(None)
prof.missing_info = []
with mock.patch.object(um, "fetch_url", fake_fetch), \
     mock.patch.object(um, "extract_requirements_from_page", fake_extract), \
     mock.patch.object(um.scholarship_repo, "get_by_official_url", lambda db, u: existing), \
     mock.patch.object(um.scholarship_repo, "create", lambda *a, **k: cnt.__setitem__("create", cnt["create"] + 1)), \
     mock.patch.object(um, "ingest_html_page", fake_ingest), \
     mock.patch.object(um.match_repo, "create", lambda *a, **k: None), \
     mock.patch.object(um, "produce_soft_preference_outcomes", lambda *a, **k: ([], [])), \
     mock.patch.object(um, "evaluate_hard_constraints", lambda p, s: [CriterionOutcome(criterion="deadline", result=CriterionResult.UNKNOWN)]):
    for i in (1, 2):
        um.run_url_match(None, uuid.uuid4(), prof, "https://example.org/s")
        print(f"  call {i}: cumulative extract(LLM) calls={cnt['extract']}, Qdrant ingests={cnt['ingest']}, new scholarship rows={cnt['create']}")

print("\n=== R-05: Qdrant point ids on re-ingest of the same page ===")
import app.data.vectors.qdrant_client as qc
import app.rag.embed as re_

stored = []


class FakeClient:
    def upsert(self, collection_name, points, timeout=None):
        stored.extend(p.id for p in points)


with mock.patch.object(qc, "ensure_collection", lambda *a, **k: None), \
     mock.patch.object(qc, "get_qdrant_client", lambda: FakeClient()), \
     mock.patch.object(re_, "embed_documents", lambda chunks: [[0.0] * 8 for _ in chunks]):
    html = "<html><body><p>" + ("Eligibility text for this scholarship. " * 60) + "</p></body></html>"
    n1 = re_.ingest_html_page(html, source_url="https://e.org", scholarship_id="s1")
    n2 = re_.ingest_html_page(html, source_url="https://e.org", scholarship_id="s1")
    print(f"  chunks per ingest={n1},{n2}; points written={len(stored)}; distinct ids={len(set(stored))}; FakeClient has no delete method and none was called")

print("\n=== V-07: discovery duplicates (sources, ingestion, coverage mocked) ===")
import app.agents.discovery.agent as da

src = [NS(id=uuid.uuid4(), domain=f"s{i}.example", source_type=NS(value="gov"), access_method=NS(value="web"), discovery_role=True) for i in (1, 2)]
by_id = {s.id: s for s in src}


def fake_listing(db, source_id, url):
    return NS(status="ok", error=None, candidates=[{"name": "Alpha"}, {"name": "Beta"}], cached=[])


def fake_ing(db, **k):
    return {"scholarship": NS(id=uuid.uuid4(), verification_status="unverified")}


prof2 = NS(target_countries=["DE", "FR"], target_fields=["engineering"], target_degree_level=None, nationality="PK")
with mock.patch.object(da.source_repo, "get_active_sources", lambda db, country=None: src), \
     mock.patch.object(da.source_repo, "get_source_by_id", lambda db, i: by_id.get(i)), \
     mock.patch.object(da, "run_ingestion", fake_ing), \
     mock.patch.object(da, "get_coverage_summary", lambda db: None), \
     mock.patch.object(da, "DiscoveryResult", lambda results, coverage: NS(results=results, coverage=coverage)):
    run = da.run_discovery(None, prof2, listing_fetch=fake_listing)
    ids = [r.scholarship_id for r in run.discovery_result.results]
    print(f"  plan steps={len(run.query_plan)} for {len(src)} sources (2 listings each = 4 distinct); result rows={len(ids)}; distinct ids={len(set(ids))}")
print("\nDone. No network attempt occurred (sockets were hard-blocked).")
