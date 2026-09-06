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

## Next phases

- **Phase 2**: run `docker-compose up` unchanged; new `/discovery` endpoint requires a seeded `source_registry` (seed list is operational config — see research.md).
- **Phase 3**: requires object storage config for `application_documents`/`generated_documents` file refs (local FS in MVP).
- **Phase 4**: no new infra; `submission_approvals` is staging-only, no live send exists in MVP.
