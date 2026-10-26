# Audit Part 3 helper (read-only, not application code).
# Run from the backend folder:
#   cd backend && PYTHONPATH=. .venv/Scripts/python ../docs/audit-part3-scripts/part3_run.py
# It calls only pure functions (no database, no LLM, no HTTP) and makes any socket use raise.
import socket
import uuid
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace as NS


def _blocked(*a, **k):
    raise AssertionError("NETWORK ATTEMPT BLOCKED")


socket.socket.connect = _blocked
socket.create_connection = _blocked
socket.getaddrinfo = _blocked

from app.models.scholarship import LifecycleStatus as LS, VerificationStatus as VS
from app.services import lifecycle as lc
from app.services.coverage import SourceSnapshot, compute_coverage_summary
from app.services.candidate_validation import compute_official_signals, unsafe_url_reason
from app.workflows.source_monitor import graph as mon
from app.workflows import cv_gen, sop_gen
from app.workflows.cv_gen import graph as cvg
from app.workflows.sop_gen import graph as sopg
from app.workflows.cv_gen.graph import AllowedFact, CvDraft, CvDraftClaim
from app.workflows.sop_gen.graph import SopDraft, SopDraftClaim

NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)

print("=== LC: can the monitor ever close or expire a stored record? ===")
src = NS(extraction_rules={}, freshness_window_days=None)
print("absence may close (default source, 10 of 10 items, baseline 10):", mon._absence_may_close(src, 10, 10))
print("explicit closed signal from the extractor's field set ({}):", mon._is_explicit_closed({"name": "X"}))
sch = NS(lifecycle_status=LS.VERIFIED, verification_status=VS.VERIFIED, last_verified_at=NOW)
past = NS(lifecycle_status=LS.VERIFIED, verification_status=VS.VERIFIED, last_verified_at=NOW, deadline=NOW.date() - timedelta(days=30))
for label, outcome in {
    "listed open": lc.FetchOutcome(fetch_succeeded=True, extraction_grounded=True, listed_state=lc.ListedState.OPEN),
    "absent, may_close False": lc.FetchOutcome(fetch_succeeded=True, extraction_grounded=True, listed_state=None),
    "fetch failed, retries exhausted": lc.FetchOutcome(fetch_succeeded=False, retries_exhausted=True),
}.items():
    print(f"  {label}: ->", lc.evaluate_fetch_outcome(past, outcome))
print("freshness on a record with a deadline 30 days in the past:", lc.evaluate_freshness(past, src, NOW))

print("=== CV: coverage counts a never-fetched source as healthy ===")
sid = uuid.uuid4()
s = compute_coverage_summary([SourceSnapshot(id=sid, country="DE", source_type="national_education", status="active")], [], now=NOW)
print("countries_covered:", s.countries_covered, "sources_checked:", s.sources_checked, "gaps:", s.gaps)

print("=== CVN: official-source signals for non-English official domains ===")
for u in ["https://www.bmbf.gouv.fr/x", "https://www.mext.go.jp/x", "https://www.bund.de/x", "https://www.ucl.ac.uk/x", "https://www.sep.gob.mx/x"]:
    print(" ", u, compute_official_signals(u, None)["domain_patterns"])
print("affiliation terms matched on German text:", compute_official_signals("https://x.de", "<p>Universitaet und Ministerium fuer Bildung, Universität Stiftung</p>")["affiliation_terms"])
print("unsafe_url_reason 127.0.0.1:", unsafe_url_reason("http://127.0.0.1/"), "| 169.254.169.254:", unsafe_url_reason("http://169.254.169.254/"))

print("=== GEN: empty drafts and invented SOP question headings pass the gate ===")
facts = [AllowedFact(text="Name: Ada Lovelace.", source="profile.name")]
print("cv ground_check, draft with zero claims:", cvg._ground_check_node({"draft": CvDraft(claims=[]), "facts": facts}))
print("sop ground_check, draft with zero claims:", sopg._ground_check_node({"draft": SopDraft(claims=[]), "facts": facts, "questions": ["Why us?"]}))
r = sopg._ground_check_node({"draft": SopDraft(claims=[SopDraftClaim(question="A question nobody asked", text="My Name is Ada Lovelace.")]), "facts": facts, "questions": ["Why us?"]})
print("sop ground_check, claim under an invented question while 'Why us?' is unanswered:", r["blocked"], r["source_trace"])
pol = sopg._polish_sop.__doc__
req = NS(key="sop_question", value="Why this programme?", value_status="known")
print("sop question derived from key 'sop_question':", sopg._extract_sop_questions(NS(requirements=[req], fields=[])))
req2 = NS(key="essay", value="Why this programme?", value_status="known")
print("sop question derived from key 'essay':", sopg._extract_sop_questions(NS(requirements=[req2], fields=[])))
print("cv _fmt_decimal(4):", cvg._fmt_decimal(4))
print("No network attempt occurred.")
