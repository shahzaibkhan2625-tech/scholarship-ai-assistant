# Audit Part 4 helper (read-only, not application code).
# Run from the backend folder (Git Bash):
#   cd backend && DATABASE_URL=postgresql://x:x@127.0.0.1:1/x JWT_SECRET_KEY=dummy GEMINI_API_KEY= QDRANT_URL= QDRANT_API_KEY= \
#     PYTHONPATH=. .venv/Scripts/python ../docs/audit-part4-scripts/part4_run.py
# It calls only pure functions and in-memory fakes (no database, no LLM, no HTTP).
# Every socket use to a non-loopback address raises. Loopback connects are allowed and counted separately,
# because Python's own asyncio event loop opens a loopback socket pair on Windows (needed only by section RQ).
import socket
import sys
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import patch

_attempts = []
_loopback = []
_real_connect = socket.socket.connect


def _blocked(*a, **k):
    _attempts.append(1)
    raise AssertionError("NETWORK ATTEMPT BLOCKED")


def _guarded_connect(self, address):
    if isinstance(address, tuple) and address and address[0] in ("127.0.0.1", "::1"):
        _loopback.append(address)
        return _real_connect(self, address)
    return _blocked()


socket.socket.connect = _guarded_connect
socket.create_connection = _blocked
socket.getaddrinfo = _blocked

NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)


def section(title):
    print(f"=== {title} ===")


# ---------------------------------------------------------------- tools/classify.py
section("CL: rules-based classify (the AI fallback is made to fail, so only the rules speak)")
import app.core.llm as llm
from app.tools.classify import classify


def _fail(*a, **k):
    raise RuntimeError("LLM disabled for this audit run")


with patch.object(llm, "generate_text", _fail):
    for text in [
        "This is not a fully funded scholarship; tuition is not covered.",
        "Thousands of students apply every year.",
        "Hosted by Duke Kunshan in a joint programme.",
        "The programme starts in September.",
        "Open to bachelor's, master's and PhD students.",
        "A master's scholarship; your supervisor should hold a PhD.",
    ]:
        r = classify(text)
        print(f"  {text!r}\n    funding={r.funding_type.value} country={r.country.value} field={r.field.value} degree={r.degree_level.value}")

# ---------------------------------------------------------------- tools/extract_listing.py
section("EL: listing extraction")
from app.tools.extract_listing import clean_html_to_text, extract_listing_from_page

print("  anchor href survives clean_html_to_text:", repr(clean_html_to_text('<a href="https://x.org/s/1">Chevening</a>')))

page = "<html><body><h1>Scholarships</h1>" + "<p>The Chevening Scholarship is open each year to applicants worldwide. </p>" * 5 + "</body></html>"
with patch.object(llm, "generate_text", lambda *a, **k: '{"candidates":[{"name":"Chevening Scholarship","deadline":"2099-01-01","funding_status":"fully_funded","detail_url":"https://invented.example/apply"}]}'):
    out = extract_listing_from_page(page, source_url="https://x.org/list")
    c = out.candidates[0]
    print("  accepted:", out.accepted_count, "| deadline kept:", c.deadline, "| funding kept:", c.funding_status, "| detail_url kept:", c.detail_url)
with patch.object(llm, "generate_text", lambda *a, **k: '[{"name":"Chevening Scholarship"}]'):
    try:
        extract_listing_from_page(page, source_url="https://x.org/list")
    except Exception as e:  # noqa: BLE001
        print("  model returns a JSON list ->", type(e).__name__, e)

# ---------------------------------------------------------------- services/normalize.py
section("NZ: normalize_record")
from app.services.normalize import normalize_record

raw = {"name": "X", "funding_status": "fully_funded", "degree_level": "MS", "country": None, "closing_status": "open"}
print("  keys that come out of normalize_record(", raw, "):", [nf.key for nf in normalize_record(raw)])
print("  sidecar says known, value empty :", normalize_record({"x": None, "x_status": "known"})[0])
print("  sidecar says unknown, value set :", normalize_record({"x": "v", "x_status": "unknown"})[0])

# ---------------------------------------------------------------- services/listing_diff.py
section("LD: listing_diff with a datetime-shaped deadline")
from app.services.listing_diff import extract_monitored_values

print("  {'deadline': '2026-12-01'} ->", extract_monitored_values({"deadline": "2026-12-01"}))
print("  {'deadline': '2026-12-01T23:59:00Z'} ->", extract_monitored_values({"deadline": "2026-12-01T23:59:00Z"}))

# ---------------------------------------------------------------- services/requirement_satisfaction.py
section("RS: requirement_satisfaction")
from app.models.requirement import Requirement, RequirementCategory as RC
from app.services.requirement_satisfaction import evaluate_requirement
from app.tools.extract_document_fields import ExtractedDocumentFields, ExtractedTestScore


def req(cat, key, value, status="known", mandatory=True):
    return Requirement(id=uuid.uuid4(), category=cat, key=key, value=value, value_status=status, confidence="inferred", mandatory=mandatory)


doc = ExtractedDocumentFields(gpa=8.5, gpa_scale=10, degree="Masters", test_scores=[ExtractedTestScore(test_type="IELTS", score=7.0)])
print("  GPA doc 8.5/10 vs requirement 8.0 (10-point):", evaluate_requirement(doc, req(RC.GPA, "min_gpa", 8.0)).status.value)
try:
    evaluate_requirement(doc, req(RC.GPA, "min_gpa", "3.0 or equivalent"))
except Exception as e:  # noqa: BLE001
    print("  GPA requirement '3.0 or equivalent' ->", type(e).__name__, e)
print("  degree: requirement \"Master's\" vs document 'Masters':", evaluate_requirement(doc, req(RC.ACADEMIC, "degree", "Master's")).status.value)
print("  test: key 'IELTS', value 6.5, doc IELTS 7.0:", evaluate_requirement(doc, req(RC.TEST, "IELTS", 6.5)).status.value)
print("  test: key 'min_ielts', value 6.5, doc IELTS 7.0:", evaluate_requirement(doc, req(RC.TEST, "min_ielts", 6.5)).status.value)
print("  test: key 'IELTS', no minimum (value None), doc IELTS 7.0:", evaluate_requirement(doc, req(RC.TEST, "IELTS", None)).status.value)
print("  language category (where the prompt puts IELTS):", evaluate_requirement(doc, req(RC.LANGUAGE, "IELTS", 6.5)).status.value)

# ---------------------------------------------------------------- services/hard_constraints.py + agents/matching
section("HC: hard_constraints")
from app.models.profile import CriterionKind, DegreeLevel as PDL
from app.models.scholarship import DegreeLevel, Scholarship
from app.services.hard_constraints import evaluate_hard_constraints


def crit(dim, value=None):
    return NS(kind=CriterionKind.HARD_CONSTRAINT, dimension=dim, operator=">=", value=value)


def prof(criteria, gpas=(), nationality=None, target=None, tests=()):
    return NS(criteria=criteria, education_records=[NS(gpa=g, gpa_scale=s) for g, s in gpas], nationality=nationality,
              target_degree_level=target, test_scores=list(tests))


def sch(reqs=(), degree=None, deadline=None):
    s = Scholarship(name="S", official_scholarship_url="https://x", degree_level=degree, deadline=deadline)
    for r in reqs:
        s.requirements.append(r)
    return s


def show(label, outcomes):
    print(f"  {label}:", [(o.criterion, o.result.value) for o in outcomes if o.criterion != "deadline"])


show("student 3.4/4.0, scholarship needs 8.0 (a 10-point scale)", evaluate_hard_constraints(prof([crit("gpa")], gpas=[(3.4, 4.0)]), sch([req(RC.GPA, "min_gpa", 8.0)])))
show("student 8.5/10, scholarship needs 3.0 (a 4-point scale)", evaluate_hard_constraints(prof([crit("gpa")], gpas=[(8.5, 10.0)]), sch([req(RC.GPA, "min_gpa", 3.0)])))
show("nationality 'Pakistan' vs requirement ['Pakistani']", evaluate_hard_constraints(prof([crit("nationality")], nationality="Pakistan"), sch([req(RC.NATIONALITY, "nationality", ["Pakistani"])])))
show("scholarship degree_level 'other', student target MS", evaluate_hard_constraints(prof([crit("degree")], target=PDL.MS), sch(degree=DegreeLevel.OTHER)))
t7 = NS(test_type=NS(value="IELTS"), status=NS(value="have"), score=7.0)
show("test requirement key 'min_ielts_score'=6.5, student has IELTS 7.0", evaluate_hard_constraints(prof([crit("test")], tests=[t7]), sch([req(RC.TEST, "min_ielts_score", 6.5)])))
show("test requirement key 'IELTS'=6.5, student entered no test scores at all", evaluate_hard_constraints(prof([crit("test")]), sch([req(RC.TEST, "IELTS", 6.5)])))
toefl = NS(test_type=NS(value="TOEFL"), status=NS(value="have"), score=100)
show("scholarship takes IELTS or TOEFL (two mandatory rows), student has TOEFL only",
     evaluate_hard_constraints(prof([crit("test")], tests=[toefl]), sch([req(RC.TEST, "IELTS", 6.5), req(RC.TEST, "TOEFL", 90)])))
a = evaluate_hard_constraints(prof([crit("gpa", 99)], gpas=[(3.4, 4.0)]), sch([req(RC.GPA, "min_gpa", 3.0)]))
b = evaluate_hard_constraints(prof([crit("gpa", 1)], gpas=[(3.4, 4.0)]), sch([req(RC.GPA, "min_gpa", 3.0)]))
print("  criterion value 99 vs 1 gives the same outcome:", [o.model_dump() for o in a] == [o.model_dump() for o in b])
show("'language' hard criterion", evaluate_hard_constraints(prof([crit("language")]), sch()))

section("MA: verdict rules (agents/matching/agent.py build_verdict)")
from app.agents.matching.agent import build_verdict
from app.schemas.match import CriterionOutcome as CO, CriterionResult as CR
from app.services.ranking import rank_matches

sid = uuid.uuid4()
dl_pass = CO(criterion="deadline", result=CR.PASS, evidence="open")
met = CO(criterion="pref", result=CR.MET, evidence="e")
unmet = CO(criterion="pref2", result=CR.UNMET, evidence="e")
print("  only the deadline checked, one soft 'met':", build_verdict(sid, [dl_pass], [met], [], []).eligibility_verdict.value)
print("  every hard check passed, no soft preferences:", build_verdict(sid, [dl_pass, CO(criterion="gpa", result=CR.PASS, evidence="e")], [], [], []).eligibility_verdict.value)
print("  every hard check passed, 1 of 3 soft met:", build_verdict(sid, [dl_pass], [met, unmet, unmet], [], []).eligibility_verdict.value)
print("  exclusion triggered ['needs GRE'] changes nothing:", build_verdict(sid, [dl_pass], [met], ["needs GRE"], []).eligibility_verdict.value)
seen = set()
for hard in ([dl_pass], [dl_pass, CO(criterion="x", result=CR.UNKNOWN)], [CO(criterion="x", result=CR.FAIL, evidence="e")], [CO(criterion="deadline", result=CR.UNKNOWN)]):
    for soft in ([], [met], [unmet], [met, unmet], [CO(criterion="p", result=CR.UNKNOWN)]):
        seen.add(build_verdict(sid, hard, soft, [], []).eligibility_verdict.value)
print("  verdicts reachable over 20 hard/soft combinations:", sorted(seen))
old = build_verdict(sid, [dl_pass], [met], [], [])
new = build_verdict(sid, [CO(criterion="gpa", result=CR.FAIL, evidence="e")], [], [], [])
print("  GET /matches order for two rows of the same scholarship (old eligible, newer not):", [v.eligibility_verdict.value for v in rank_matches([new, old])])

# ---------------------------------------------------------------- agents/research_qa
section("QA: answer labels")
import app.agents.research_qa.agent as qa
from app.rag.retrieve import RetrievedChunk

chunks = [RetrievedChunk(text="The minimum GPA is 3.0. There is no application fee for this scholarship.", source_url="https://x.org", score=0.9, retrieved_at=NOW.isoformat())]
for ans in ["No.", "Yes", "3.0", "The minimum GPA required is 3.5", "There is no application fee for this scholarship."]:
    with patch.object(qa, "retrieve", lambda *a, **k: chunks), patch.object(llm, "generate_text", lambda *a, **k: ans):
        r = qa.answer_question("q", "sid")
        print(f"  model says {ans!r} -> {r.confidence.value}")

# ---------------------------------------------------------------- discovery plan
section("DA: build_query_plan against the five seeded sources")
import app.agents.discovery.agent as da

seeds = [
    NS(id=uuid.uuid4(), name="DAAD", country="DE", discovery_role=False, source_type="national_education", access_method="web"),
    NS(id=uuid.uuid4(), name="EACEA", country="EU", discovery_role=True, source_type="international_org", access_method="web"),
    NS(id=uuid.uuid4(), name="Hungaricum", country="HU", discovery_role=False, source_type="gov", access_method="web"),
    NS(id=uuid.uuid4(), name="HEC", country="PK", discovery_role=True, source_type="gov", access_method="web"),
    NS(id=uuid.uuid4(), name="KAUST", country="SA", discovery_role=False, source_type="university", access_method="web"),
]


def fake_active(db, country=None, **k):
    return [s for s in seeds if country is None or s.country == country]


def plan_for(**p):
    base = dict(target_countries=[], target_fields=[], target_degree_level=None, nationality=None)
    base.update(p)
    with patch.object(da.source_repo, "get_active_sources", fake_active):
        return [(s.dimension, s.value, next(x.name for x in seeds if x.id == s.source_id)) for s in da.build_query_plan(None, NS(**base))]


print("  target_countries=['Germany']:", plan_for(target_countries=["Germany"]))
print("  target_countries=['DE']:", plan_for(target_countries=["DE"]))
print("  target_countries=['PK']:", plan_for(target_countries=["PK"]))
print("  nationality='Pakistani' only:", plan_for(nationality="Pakistani"))
print("  fields=['physics'], countries=['Germany']:", plan_for(target_fields=["physics"], target_countries=["Germany"]))

# ---------------------------------------------------------------- connectors/official_fetch cache
section("OF: the cross-run listing cache (T135/A9) when ingestion rows exist")
import app.sources.connectors.official_fetch as of
from app.models.source import FetchStatus
from app.tools.web_fetch import FetchResult

src = NS(id=uuid.uuid4(), domain="www.eacea.ec.europa.eu", update_frequency="annual", listing_page_url="https://www.eacea.ec.europa.eu/scholarships/emjmd-catalogue_en", extraction_rules={})
url = src.listing_page_url
extraction_row = NS(status=FetchStatus.OK, items_found=4, fetched_url=url, started_at=NOW - timedelta(minutes=5))
ingestion_row = NS(status=FetchStatus.OK, items_found=1, fetched_url=None, started_at=NOW - timedelta(minutes=4))
calls = {"fetch": 0}


def fake_fetch(u):
    calls["fetch"] += 1
    return FetchResult(url=u, success=True, status_code=200, html="<html></html>", final_url=u)


def run_cache(rows):
    calls["fetch"] = 0
    with patch.object(of.source_repo, "get_source_by_id", lambda db, i: src), \
         patch.object(of.source_repo, "get_fetch_logs_for_source", lambda db, i: rows), \
         patch.object(of.source_repo, "get_scholarship_sources_by_source", lambda db, i: []), \
         patch.object(of.source_repo, "log_fetch", lambda db, d: None), \
         patch.object(of.source_repo, "mark_source_failing", lambda db, i: None), \
         patch.object(of.source_repo, "get_active_sources", lambda db, **k: []), \
         patch.object(of, "datetime", NS(now=lambda tz=None: NOW)):
        res = of.fetch_and_extract_listing(None, src.id, None, fetch=fake_fetch,
                                            extract_listing=lambda html, source_url: NS(accepted_count=1, rejected_count=0, rejected_names=[], candidates=[NS(model_dump=lambda **k: {"name": "X"})]))
        return res.status, calls["fetch"]


print("  only the extraction row (control)      -> status, fetches:", run_cache([extraction_row]))
print("  ingestion row written after extraction -> status, fetches:", run_cache([ingestion_row, extraction_row]))

# ---------------------------------------------------------------- retry on a 404
section("RP: retry policy on a permanent 404")
sleeps = []
calls["fetch"] = 0


def fake_404(u):
    calls["fetch"] += 1
    return FetchResult(url=u, success=False, status_code=404, error="Server responded with HTTP 404")


with patch.object(of.source_repo, "get_source_by_id", lambda db, i: src), patch.object(of.source_repo, "log_fetch", lambda db, d: None), \
     patch.object(of.source_repo, "mark_source_failing", lambda db, i: None):
    res = of.fetch_official(None, src.id, "https://www.eacea.ec.europa.eu/missing", fetch=fake_404, sleep=sleeps.append)
print("  HTTP 404 -> attempts made:", calls["fetch"], "| sleeps between them:", sleeps, "| success:", res.success)

# ---------------------------------------------------------------- source_repo.record_or_bump_candidate_source
section("SR: candidate-source de-duplication for a 'www.' link")
import app.data.repositories.source_repo as sr
from app.models.source import CandidateSourceStatus

stored = []


def run_bump(link_url, domain):
    created = []
    with patch.object(sr, "get_source_by_domain", lambda db, d: None), \
         patch.object(sr, "list_candidate_sources", lambda db, status=None: list(stored)), \
         patch.object(sr, "create_candidate_source", lambda db, data: created.append(data) or stored.append(NS(url=data.url, status=CandidateSourceStatus.PENDING, signals=dict(data.signals))) or stored[-1]):
        sr.record_or_bump_candidate_source(NS(commit=lambda: None, refresh=lambda x: None), domain=domain, url=link_url, discovered_from="https://list", matched_keyword="scholarship")
    return len(created)


from app.tools.detect_candidate_links import find_candidate_links

html = '<a href="https://www.fundme.org/scholarships">Scholarship fund</a>'
sig = find_candidate_links(html, source_url="https://www.hec.gov.pk/list", known_domains=set())[0]
print("  detector output: domain =", sig.domain, "| url =", sig.url)
print("  rows created on 1st sighting:", run_bump(sig.url, sig.domain), "| on 2nd sighting (same link):", run_bump(sig.url, sig.domain), "| on 3rd:", run_bump(sig.url, sig.domain))
stored.clear()
sig2 = find_candidate_links('<a href="https://fundme.org/scholarships">Scholarship fund</a>', source_url="https://www.hec.gov.pk/list", known_domains=set())[0]
print("  control, link without 'www.': 1st:", run_bump(sig2.url, sig2.domain), "| 2nd:", run_bump(sig2.url, sig2.domain))

# ---------------------------------------------------------------- source domain regex
section("SC: SourceRegistryCreate accepts internal addresses as a 'bare hostname'")
from app.schemas.source import SourceRegistryCreate

for d in ["169.254.169.254", "10.0.0.5", "localhost", "internal.corp"]:
    try:
        SourceRegistryCreate(name="x", source_type="gov", official_status="official", domain=d, access_method="web", reliability_level="high")
        print(f"  domain {d!r}: accepted")
    except Exception as e:  # noqa: BLE001
        print(f"  domain {d!r}: rejected ({type(e).__name__})")

# ---------------------------------------------------------------- source_validate heuristics
section("SV: source_validate heuristics")
from app.workflows.source_validate.graph import _heuristics_node

for u in ["https://www2.daad.de/x", "https://www.eacea.ec.europa.eu/x", "https://stipendiumhungaricum.hu/apply/", "https://www.hec.gov.pk/x", "https://www.kaust.edu.sa/x", "https://evil.example@good.org/x", "https://anyone.org/"]:
    print(" ", u, "->", _heuristics_node({"url": u})["heuristics"])

# ---------------------------------------------------------------- orchestrator
section("OR: orchestrator routing")
from app.orchestration.main_agent import route_message

for text, kw in [("Please write my SOP for this scholarship", {}), ("What is the deadline for https://x.org/s?", {}),
                 ("What is the deadline for https://x.org/s?", {"active_scholarship_id": uuid.uuid4()})]:
    d = route_message(text, **kw)
    print(f"  {text!r} {'(with active scholarship)' if kw else ''} -> clarification={d.needs_clarification} actions={[a.intent.value for a in d.actions]}")

# ---------------------------------------------------------------- submit_prep
section("SP: submit_prep fingerprint and staging with nothing attached")
from app.workflows.submit_prep import graph as sp
from app.services.submission_approval import is_submission_authorized

fp = sp.compute_content_fingerprint([], [])
print("  fingerprint of an empty package:", fp[:16] + "...")
print("  an approval of it authorizes the same empty package:", is_submission_authorized([NS(submission_scope="final-submission", content_fingerprint=fp)], "final-submission", fp))
print("  staged output for the empty package:", sp._map_for_staging_node({"authorized": True, "application_id": uuid.uuid4(), "submission_scope": "final-submission", "content_fingerprint": fp, "application_documents": [], "generated_documents": []})["staged"])

# ---------------------------------------------------------------- dedup embedding cost
section("DD: dedup embedding calls for one candidate")
from app.services.dedup import DedupCandidate, find_duplicate

n = {"c": 0}


def fake_embed(t):
    n["c"] += 1
    return [1.0, 0.0] if "a" in t else [0.0, 1.0]


existing = [DedupCandidate(id=str(i), name=f"n{i}", text=f"text {i}") for i in range(5)]
find_duplicate(DedupCandidate(id="c", name="new", text="candidate text"), existing, embed=fake_embed)
print("  existing rows with text: 5 -> embed calls:", n["c"])

# ---------------------------------------------------------------- security / auth
section("AU: password handling")
from app.core.security import hash_password, verify_password
from app.schemas.auth import SignupRequest

print("  SignupRequest with an empty password accepted:", bool(SignupRequest(email="a@b.co", password="")))
try:
    h = hash_password("x" * 100)
    print("  100-char password hashed; the first 72 bytes decide:", verify_password("x" * 72 + "DIFFERENT", h))
except Exception as e:  # noqa: BLE001
    print("  100-char password ->", type(e).__name__, e)
print("  'Ada@x.com' vs 'ada@x.com' normalised to the same address:", SignupRequest(email="Ada@x.com", password="p").email == SignupRequest(email="ada@x.com", password="p").email)

# ---------------------------------------------------------------- PDF
section("PP: owner-password-only PDF through parse_pdf")
import pymupdf
from app.tools.pdf_parse import PdfParseError, parse_pdf

d = pymupdf.open()
d.new_page().insert_text((72, 72), "Cumulative GPA 3.60 / 4.00")
raw = d.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="")
chk = pymupdf.open(stream=raw, filetype="pdf")
print("  pymupdf says: needs_pass =", chk.needs_pass, "| is_encrypted =", chk.is_encrypted, "| text readable =", "3.60" in chk[0].get_text())
print("  file really is encrypted (/Encrypt in the bytes):", b"/Encrypt" in raw)
try:
    print("  parse_pdf (owner password only) ->", parse_pdf(raw).text)
except PdfParseError as e:
    print("  parse_pdf (owner password only) -> PdfParseError:", e)
d2 = pymupdf.open()
d2.new_page().insert_text((72, 72), "secret")
raw2 = d2.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
try:
    parse_pdf(raw2)
except PdfParseError as e:
    print("  parse_pdf (user password set) -> PdfParseError:", e)

# ---------------------------------------------------------------- request id on a crash
section("RQ: X-Request-ID on an unhandled error")
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from app.core.request_id import RequestIDMiddleware


def ok(request):
    return PlainTextResponse("ok")


def boom(request):
    raise RuntimeError("boom")


app = Starlette(routes=[Route("/ok", ok), Route("/boom", boom)])
app.add_middleware(RequestIDMiddleware)
client = TestClient(app, raise_server_exceptions=False)
r1, r2 = client.get("/ok"), client.get("/boom")
print("  200 response has X-Request-ID:", "x-request-id" in r1.headers, "| unhandled-error 500 has X-Request-ID:", "x-request-id" in r2.headers, "(status", r2.status_code, ")")

print(f"Non-loopback network attempts: {len(_attempts)}. Loopback connects (asyncio socket pair, section RQ only): {len(_loopback)}.")
sys.stdout.flush()
