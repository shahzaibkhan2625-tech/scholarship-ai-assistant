"""T135 Slice 3 — end-to-end live verification.

**Scope note (do not read a green run here as "US4 complete"):** this proves
spec.md US4 Acceptance Scenario 1 only — ranked results drawn from at least
two distinct governed source types, each with source, source type, and
verification status. Scenario 3 (an unrecognized site gets recorded as a
`pending` candidate source) is a different capability and remains a
separate, still-open item; see `agents/discovery/agent.py`'s module
docstring "Scope note".

Unlike the rest of the suite, this test deliberately calls the REAL
configured Gemini API — same category as `test_live_services.py` (T138):
excluded from the normal run via the `smoke` marker, picked up by
deep-ci.yml's weekly `pytest -m smoke tests/smoke/`. Only the raw network
transport (`httpx.get`, inside `app.tools.web_fetch`) is mocked, dispatching
by domain to HTML reconstructed from the real markup shapes T135's Phase A
found live-fetching EACEA (international_org) and HEC Pakistan (gov) — not
byte-for-byte captures, per this project's "no external test-data
dependency" convention (`test_pdf_parse.py`). Everything above the network
layer — the real LLM extraction call, the deterministic grounding check,
`run_ingestion`, and the real `POST /discovery` endpoint — runs unmocked.

Cost: exactly 2 real Gemini calls per run (one per dedicated source; T135/A9's
per-run memoization guarantees no more than one call per unique source_id
regardless of how many profile dimensions match it). The 5 real seeded
sources are also reachable via this profile's other active-source lookups,
but `_fake_httpx_get`'s fallback returns empty content for any URL that
isn't one of this test's two dedicated domains, which stays under
`extract_listing`'s minimum-viable-text-length gate — zero extra LLM calls
for those.

**Program names below are deliberately fictional, not the real Erasmus
Mundus/HEC catalogue entries.** The very first live run of this test used
the real catalogue names and failed one side of the assertion: the LLM
"corrected" a well-known real name into a slightly different phrasing it
already knew from training data, which the — correctly strict —
`ground_check(..., threshold=1.0)` then rejected as ungrounded. That's the
grounding check doing its job, not a bug, but a real, famous program name is
a bad fixture choice: the LLM has a real-world "improvement" to substitute
for it. An invented name removes that specific failure mode.

**Do not re-run this test repeatedly to "confirm" it's stable.** This
free-tier Gemini key has a hard cap of 20 real requests/day
(`GenerateRequestsPerDayPerProjectPerModel-FreeTier`) shared across this
whole project. One clean run is the intended and sufficient evidence this
feature works end-to-end — that was T135 Slice 3's actual, approved scope.
A repeated-runs stability/repeatability study is a separate, deliberately
quota-budgeted exercise (e.g. spread across several days, or with a paid
key) and must never be improvised mid-slice: doing exactly that during this
test's own development burned the day's quota and produced several
uninterpretable failures (a mix of possible transient rate-limiting and
possible genuine grounding rejections, impossible to tell apart after the
fact) immediately followed by an explicit 429 from an unrelated diagnostic
call. Treat any failure here as "investigate before re-running," not
"re-run until green."
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.data.repositories import source_repo
from app.schemas.source import SourceRegistryCreate

pytestmark = pytest.mark.smoke

_EACEA_LIKE_HTML = """
<html><body>
<ul class="ecl-listing">
  <li class="ecl-card">
    <div class="ecl-card__body">
      <div class="ecl-content-block ecl-card__content-block">
        Erasmus Mundus Joint Master in Applied Cryospheric Systems and Polar Ecology
      </div>
    </div>
  </li>
  <li class="ecl-card">
    <div class="ecl-card__body">
      <div class="ecl-content-block ecl-card__content-block">
        Joint Master's Programme in Comparative Deep-Sea Linguistics
      </div>
    </div>
  </li>
</ul>
</body></html>
"""

_HEC_LIKE_HTML = """
<html><body>
<ul id="myUL" class="card-info-container list-view-display">
  <li class="card card-info-listing" data-position="Highland Frontier Scholarship for Applied Atmospheric Meteorology">
    <div class="desc">Highland Frontier Scholarship for Applied Atmospheric Meteorology</div>
  </li>
  <li class="card card-info-listing" data-position="National Fellowship in Subterranean Archaeological Studies">
    <div class="desc">National Fellowship in Subterranean Archaeological Studies</div>
  </li>
</ul>
</body></html>
"""


def _fake_httpx_get(url: str, **_kwargs) -> SimpleNamespace:
    if "eacea-smoke" in url:
        html = _EACEA_LIKE_HTML
    elif "hec-smoke" in url:
        html = _HEC_LIKE_HTML
    else:
        html = "<html></html>"  # any other active source: empty -> never a real LLM call
    return SimpleNamespace(status_code=200, text=html)


def test_discovery_endpoint_extracts_real_listing_candidates_across_two_source_types(authed_user, db_session_factory):
    client, headers = authed_user["client"], authed_user["headers"]
    setup_session = db_session_factory()

    eacea_country = f"Smoke-EACEA-{uuid.uuid4().hex[:8]}"
    hec_country = f"Smoke-HEC-{uuid.uuid4().hex[:8]}"

    eacea_source = source_repo.upsert_source_by_domain(
        setup_session,
        SourceRegistryCreate(
            name="Smoke Test EACEA-like Source",
            source_type="international_org",
            official_status="official",
            domain=f"eacea-smoke-{uuid.uuid4().hex}.example.invalid",
            access_method="web",
            reliability_level="high",
            status="active",
            discovery_role=True,
            country=eacea_country,
        ),
    )
    hec_source = source_repo.upsert_source_by_domain(
        setup_session,
        SourceRegistryCreate(
            name="Smoke Test HEC-like Source",
            source_type="gov",
            official_status="official",
            domain=f"hec-smoke-{uuid.uuid4().hex}.example.invalid",
            access_method="web",
            reliability_level="high",
            status="active",
            discovery_role=True,
            country=hec_country,
        ),
    )

    put_response = client.put("/profile", headers=headers, json={"target_countries": [eacea_country, hec_country]})
    assert put_response.status_code == 200

    with patch("app.tools.web_fetch.httpx.get", side_effect=_fake_httpx_get):
        response = client.post("/discovery", headers=headers)

    assert response.status_code == 200
    body = response.json()

    our_results = [
        item for item in body["results"] if item["source_id"] in {str(eacea_source.id), str(hec_source.id)}
    ]
    assert len(our_results) >= 2, f"expected candidates from both dedicated sources, got: {body['results']}"

    source_types = {item["source_type"] for item in our_results}
    assert source_types == {"international_org", "gov"}, (
        f"expected ranked results spanning >=2 distinct source_types (spec.md US4 Scenario 1), got {source_types}"
    )

    assert all(item["verification_status"] == "unverified" for item in our_results), (
        "T135/A3: a listing page is never an official confirmation -- every listing-derived "
        "candidate must be unverified, never fabricated as verified"
    )

    assert body["coverage"]["claims_complete_coverage"] is False
