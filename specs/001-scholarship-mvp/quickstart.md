# Quickstart: Scholarship AI Assistant — MVP Core Platform

This quickstart covers running and manually verifying **Phase 1** (Profile + URL Match + Q&A), the first slice per blueprint phase ordering. Later phases (Discovery, Documents/CV/SOP, Planner/Tracker/Assistant) follow the same run/verify pattern once their endpoints land — see `contracts/openapi.yaml` tags for what exists per phase.

## Prerequisites (Phase 0, already satisfied)

- Docker + `docker-compose up` runs the FastAPI backend.
- `.env` (repo root) has `DATABASE_URL` (Neon Postgres) and `JWT_SECRET_KEY` set — see `.env.example`.
- Alembic migrations applied: `cd backend && alembic upgrade head`.
- Existing tests pass: `cd backend && pytest`.

Phase 1 additionally requires:
- A Qdrant Cloud cluster URL + API key (for RAG ingestion/retrieval) — add to `.env` when the `rag/` module lands.
- MCP `web_fetch` server reachable (for `url_match`'s `fetch` node) — provider choice is deferred (see research.md D-items), but the tool interface is fixed regardless.

## Running the stack

```bash
docker-compose up --build
# API available at http://localhost:8000
# OpenAPI docs at http://localhost:8000/docs
```

## Manual verification against spec.md acceptance scenarios

### US1 — Build a constraint-typed profile

```bash
# 1. Sign up + log in (Phase 0, existing)
curl -X POST localhost:8000/auth/signup -H 'Content-Type: application/json' \
  -d '{"email":"student@example.com","password":"..."}'
TOKEN=$(curl -X POST localhost:8000/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"student@example.com","password":"..."}' | jq -r .access_token)

# 2. Create profile with a hard-constraint criterion (Acceptance Scenario 1)
curl -X PUT localhost:8000/profile -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"nationality":"Pakistani","target_degree_level":"MS","target_fields":["Computer Science"],"target_countries":["Germany"]}'
curl -X POST localhost:8000/profile/criteria -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"dimension":"nationality","operator":"=","value":"Pakistani","kind":"hard_constraint"}'

# Verify: GET /profile shows nationality retrievable as a hard constraint (Scenario 1).
# Verify: leave GRE status unset -> GET /profile lists it under missing_info, not silently omitted (Scenario 3).
# Verify: PUT /profile with a new IELTS score updates without re-entering the whole profile (Scenario 2).
# Verify: re-POST /profile/criteria with kind changed soft_preference -> hard_constraint reclassifies in place (Scenario 4).
```

### US2 — Match a known scholarship by URL

```bash
curl -X POST localhost:8000/scholarships/match-url -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"url":"https://example-official-scholarship.org/master-scholarship"}'
```

- **Expected (Scenario 1)**: response is a `UrlMatchResult` with `verdict.eligibility_verdict`, `verdict.match_strength`, and per-criterion `hard_constraints`/`soft_preferences` arrays, each with `evidence`.
- **Expected (Scenario 2)**: a profile that fails a hard constraint (e.g. wrong nationality) returns `eligibility_verdict: not`, and the failing criterion appears in `hard_constraints` with `result: fail` — regardless of any strong soft-preference match.
- **Expected (Scenario 3)**: a criterion absent from the source page appears with `result: unknown`, never defaulted to pass/fail.
- **Expected (Scenario 4)**: an unreachable URL returns HTTP 422 with `FetchFailure`, never a fabricated "not eligible" verdict.

### US3 — Ask grounded questions

```bash
curl -X POST localhost:8000/scholarships/$SCHOLARSHIP_ID/qa -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"question":"What is the stipend?"}'
```

- **Expected (Scenario 1)**: `confidence: verified`, `source_url` + `last_verified_at` populated.
- **Expected (Scenario 2)**: asking about something the source never mentions (e.g. spousal policy) returns `confidence: unknown` and an explicit "could not confirm" `answer_text` — never a guess.
- **Expected (Scenario 3)**: an answer synthesized from two related-but-not-identical statements is labeled `confidence: inferred`.
- **Expected (Scenario 4)**: a question unrelated to any scholarship/sourced content is not answered as a grounded fact (the Main Agent's routing should decline or redirect rather than dispatch to Q&A).

## Where the corresponding eval cases live

Real eval cases attach starting Phase 1 (constitution Principle V / blueprint §25.1) in `backend/evals/cases/`:
- `matching_correctness/` — known profile × known scholarship → expected verdict fixture matrix (also the guardrail-violation build-breaker: any test asserting an LLM-overridden hard-constraint result MUST fail the build).
- `qa_groundedness/` — fixed question/source pairs with expected confidence labels.

## Verifying the Constitution's non-negotiables end-to-end (Phase 1 slice)

- [ ] A hard-constraint failure never flips to a passing verdict regardless of soft-preference strength (Principle II) — covered by `matching_correctness` fixtures.
- [ ] Every `MatchVerdict`/`QaAnswer` response includes a confidence/verification label (Principle I, SC-008).
- [ ] No endpoint in this phase calls a live external submission API (Principle III — not applicable yet; no such endpoint exists until Phase 4, and even then it's approval-gated only).
- [ ] `url_match`'s `fetch` node only calls the registry-governed `web_fetch` tool, never an arbitrary unvetted domain treated as authoritative (Principle IV — registry itself lands in Phase 2, but the tool boundary is already the only fetch path in Phase 1).

## Known operational caveat: Qdrant Cloud timeouts (Phase 1+ RAG ingestion)

`match-url`, `qa`, and `discovery` all depend on Qdrant Cloud for RAG ingestion/retrieval. Two things to know before running the commands below for real (see `docs/t130-verification-log.md` for the live investigation that found these):

- A free-tier Qdrant cluster **auto-suspends on inactivity**. If `match-url` returns `500` with `qdrant_client...timed out` in the logs, check whether the cluster is paused before assuming a code bug.
- Even on a healthy/just-resumed cluster, the **first** `match-url` call may still `500` — `QdrantClient` is constructed with no explicit `timeout` (`backend/app/data/vectors/qdrant_client.py:29`), so it inherits httpx's ~5s default, which can be too short for a bulk chunk-upsert. Retrying the same call typically succeeds once the cluster is warm. This is a known, unfixed gap (not addressed by T130, flagged for follow-up).
- **`POST /scholarships/{id}/qa` currently fails 100% of the time**, not intermittently: `search_chunks` filters `query_points` by `scholarship_id` but no payload index for that field is ever created, so Qdrant rejects every query with `400: Index required but not found for "scholarship_id"`. Do not expect Scenario 1/2/4 below to pass until this is fixed.

## US4 — Discover scholarships from governed sources

```bash
curl -X POST localhost:8000/discovery -H "Authorization: Bearer $TOKEN"
```

- **Expected (Scenario 1)**: response `results[]` items each carry `scholarship_id`, `source_id`, `source_type`, `verification_status`, drawn from at least two distinct `source_type` values (requires a profile with `nationality`/`target_degree_level`/`target_fields`/`target_countries` set — see US1).
  - **Known gap, currently always empty**: as of this writing, every registered source uses `access_method: web`, and `discovery/agent.py`'s web-source path deliberately does not parse listing pages into candidates yet (see `agent.py:23-34`'s "Interpretation note" and `docs/t130-verification-log.md`'s US4 investigation) — so `results: []` is the actual, current, 100%-reproducible response regardless of profile. This is not documented anywhere as an accepted Phase 2 scope decision; treat it as an open defect against Phase 2, not expected behavior.
- **Expected (Scenario 2)**: `coverage` accompanies every response with `sources_configured`, `sources_checked`, `sources_failed`, `gaps[]`, and `claims_complete_coverage: false` always — never true. (Verified live — this part works correctly.)
- **Expected (Scenario 3)**: a site outside the governed Source Registry is recorded as a pending candidate (`GET /sources/candidates` lists it, `status: pending`) and never appears in `/sources` (active only) until `POST /sources/candidates/{id}/approve`. **Not currently exercisable**: no live code path calls the candidate-recording repository function (confirmed by `grep`), so this can't be demonstrated with the code as it stands.
- **Expected (Scenario 4)**: a source fetch failure appears in `coverage.gaps`/`sources_failed`, never suppresses the rest of the result set. (Verified live.)
- **Expected (Scenario 5)**: conflicting details from two governed sources for the same scholarship are surfaced (via `scholarship_fields.value_status: conflicting`) unless the official>recent>reliable rule resolves it deterministically. Not exercisable until Scenario 1's gap is closed (no results are ever produced to conflict).

```bash
curl localhost:8000/sources -H "Authorization: Bearer $TOKEN"
curl localhost:8000/sources/candidates -H "Authorization: Bearer $TOKEN"
```
- **Expected**: `/sources` never returns a `pending`/`disabled`/`failing` row; `/sources/candidates` never returns anything already promoted. (Verified live — passes.)

## US5 — Documents, CV, SOP

```bash
curl -X POST localhost:8000/applications -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"scholarship_id":"'$SCHOLARSHIP_ID'"}'
# -> APPLICATION_ID

curl -X POST localhost:8000/applications/$APPLICATION_ID/documents \
  -H "Authorization: Bearer $TOKEN" -F "type=transcript" -F "file=@transcript.pdf"
```
- **Expected (Scenario 1)**: response `UploadedDocument` has `satisfies_requirement_id` set to the matching requirement when the scholarship requires a transcript.
  - **Coverage gap** (see `docs/t130-verification-log.md`): matching only fires against a `gpa` or `academic`-category requirement (`doc_pipeline/graph.py:59`), and only when the deterministic evaluator returns `SATISFIED`. This is covered by an automated fixture test (`test_document_generation_flow.py::test_scenario_1_...`), but no *live* (real-extraction) run has yet demonstrated the satisfied path — it needs a real or purpose-built scholarship whose extracted GPA/degree requirement actually matches the uploaded transcript's facts.
- **Expected (Scenario 2)**: an uploaded doc whose extracted GPA disagrees with the profile's GPA populates `inconsistency_flags[]` rather than silently overwriting either value. (Verified live — passes.)
- **Expected**: a corrupt/unparseable file returns **422**, never a silent partial success. Note: its `application_documents` row is still persisted (durably, before parsing) even on a 422 — factor that into any cleanup script. (Verified live — passes.)

```bash
curl -X POST localhost:8000/applications/$APPLICATION_ID/generate/cv -H "Authorization: Bearer $TOKEN"
curl -X POST localhost:8000/applications/$APPLICATION_ID/generate/sop -H "Authorization: Bearer $TOKEN"
```
- **Expected (Scenario 3)**: generated document respects the scholarship's specified CV format / SOP questions (spot-checked against `requirements` for that scholarship).
- **Expected (Scenario 4)**: `GeneratedDocument.source_trace[]` traces every factual claim back to profile/document data — no entry lacking a source.
- **Expected (Scenario 5)**: when required profile/document info is missing, response is **409**, not a document with invented content filled in. (Verified live — both generation calls correctly 409'd on an untraceable claim.)

## US6 — Application Planning & Assistant

```bash
curl -X POST localhost:8000/applications/$APPLICATION_ID/plan -H "Authorization: Bearer $TOKEN"
```
- **Expected (Scenario 1)**: every `checklist[]` item has exactly one `readiness_label` ∈ {Complete, Missing, User-must-obtain, AI-can-generate, Needs-human-review, Needs-official-verification}. (Verified live — passes.)
- **Expected (Scenario 2)**: missing items are distinguishable (label) from AI-generatable ones. (Verified live — passes.)

```bash
curl -X POST localhost:8000/applications/$APPLICATION_ID/assistant/next-step -H "Authorization: Bearer $TOKEN"
```
- **Expected (Scenario 5)**: `AssistantStepResult.result` for an equivalent step matches the deterministic-mode endpoint's own response shape (agentic/deterministic parity). (Verified live — passes; also covered by existing integration tests, commit e64e001.)

```bash
curl -X POST localhost:8000/applications/$APPLICATION_ID/submission-approvals \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"submission_scope":"<unique-id-for-this-submission>","notes":"..."}'
```
- **Expected (Scenario 3)**: no endpoint anywhere transmits externally before this call exists for the specific `submission_scope`. (No live-send endpoint exists anywhere in the MVP — structurally true.)
- **Expected (Scenario 4)**: this approval's `submission_scope` is exact-string-bound (`content_fingerprint` present) — a second submission needs its own approval; re-using the scope for different content should not silently pass. (Verified live — two different `submission_scope` values both succeeded independently, no carry-forward.)

## Non-negotiables checklist — Phase 2-4 additions

- [ ] No discovery/generation code path treats a `candidate_sources` (pending) row as authoritative (Principle IV) — only `source_registry` with `status: active`. (Currently moot for discovery — see US4's known gap above; no candidate rows are ever produced by the live path yet.)
- [ ] `coverage.claims_complete_coverage` is `false` on every response, structurally (Pydantic `Literal[False]`), not just by convention. (Verified live.)
- [ ] Every fact in a generated CV/SOP traces to `source_trace[]`; a gap is reported (409) rather than invented (Principle I/anti-fabrication). (Verified live.)
- [ ] `POST /applications/{id}/submission-approvals` is the *only* write in the entire Phase 2-4 surface with any external-transmission semantics, and even it is staging-only — no code path calls a live external submission API (Principle III, FR-APP-4).
- [ ] An approval's `submission_scope` never carries forward to a later/different submission (FR-APP-3). (Verified live.)

## Next phases

- **Phase 2**: run `docker-compose up` unchanged; new `/discovery` endpoint requires a seeded `source_registry` (seed list is operational config — see research.md). **See the known gap above: it currently returns `results: []` unconditionally, regardless of seeding, until web-source listing-page extraction is implemented.**
- **Phase 3**: requires object storage config for `application_documents`/`generated_documents` file refs (local FS in MVP).
- **Phase 4**: no new infra; `submission_approvals` is staging-only, no live send exists in MVP.
