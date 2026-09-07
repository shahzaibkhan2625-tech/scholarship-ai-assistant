# Tasks: Scholarship AI Assistant — MVP Core Platform

**Input**: Design documents from `specs/001-scholarship-mvp/` (plan.md, spec.md, data-model.md, contracts/openapi.yaml, research.md, quickstart.md)
**Prerequisites**: plan.md (required, present), spec.md (required, present), data-model.md (present), contracts/openapi.yaml (present)
**Constitution**: `.specify/memory/constitution.md` v1.0.0 — Principles I–V are NON-NEGOTIABLE and drive several task requirements below (verdict-correctness matrix, guardrail tests, evidence-required, registry-only sourcing, human-approval gating).

**Tests**: **NOT optional for this feature.** The tasks-template default ("tests are optional unless requested") is overridden here per explicit user instruction and constitution Principle V ("Tests and evals are distinct and both mandatory"). Every user-story phase below includes contract tests, integration tests mapped to spec.md's Acceptance Scenarios, and — for US2 — the constitutionally-mandated hard-constraint verdict-correctness test matrix and guardrail-violation tests (Principle II: build-breaking, not a warning).

**Organization**: Tasks are grouped by blueprint phase (plan.md §"Summary" phase-ordering table), each phase covering the user story/stories the blueprint assigns to it: Phase 1 = US1+US2+US3 (P1), Phase 2 = US4 (P2), Phase 3 = US5 (P3), Phase 4 = US6 (P4). Phase 0 records already-completed foundation work for traceability.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US6 per spec.md; omitted for Phase 0 and foundational/cross-cutting tasks
- Every task names an exact file path (or, for LangGraph workflow / OpenAI-Agents-SDK agent packages, the package's entrypoint file)

## Path Conventions

Backend-only modular monolith (per plan.md "Structure Decision"): `backend/app/`, `backend/tests/`, `backend/migrations/versions/`, `backend/evals/`. No frontend paths in this file — frontend is an explicitly later phase per plan.md.

---

## Resolution notes (sequencing decisions made to keep this breakdown buildable in phase order)

These are small, reversible scheduling calls, not architectural changes — flagged here for traceability per constitution Principle V (spec-driven, auditable decisions), not silently applied:

1. **`universities` / `programs` / `scholarship_fields` land in Phase 1** (`models/scholarship.py`), not Phase 2, because `scholarships.university_id`/`program_id` are FKs that need their referenced tables to exist for URL-match (US2) to persist a scholarship at all. **`scholarship_sources`** stays in Phase 2 (`models/source.py`) because it FKs `source_registry`, which doesn't exist until Phase 2. This reads data-model.md's "section 2 minus registry tables" clause literally.
2. **Q&A (US3) has no persisted table** per data-model.md §5 ("stateless structured responses... not stored entities in MVP") — no model/migration task is generated for US3; it is schema-only (`schemas/qa.py`) plus the agent.
3. **A minimal `applications` table + `POST /applications` ships in Phase 3**, not Phase 4, because `contracts/openapi.yaml`'s `/applications/{application_id}/documents` (Phase 3) requires a real `application_id` to exist. Phase 3 creates `applications(id, user_id, scholarship_id, status, created_at)` and the bare create endpoint only; Phase 4 adds `tasks` + `submission_approvals` and the remaining planner/tracker/assistant/approval endpoints onto that same table/router file. This avoids either an unenforced FK or moving the full US6 feature earlier.
4. **`workflows/source_monitor/`** is explicitly "designed only, NOT built in MVP (Phase 5)" per plan.md's Project Structure — no task is generated for it anywhere below.

---

## Phase 0: Foundation (COMPLETED)

**Purpose**: Already-implemented groundwork this plan explicitly reuses unchanged (plan.md Summary: "reusing the already-implemented Phase 0 foundation... without redesigning it"). Recorded here as done tasks so tasks.md is a complete project record.

- [X] T001 Initialize backend project with uv + Python 3.12 in `backend/pyproject.toml`, `backend/.python-version`, `backend/uv.lock`
- [X] T002 Create FastAPI app skeleton with `/health` endpoint in `backend/app/main.py`
- [X] T003 [P] Create modular-monolith package skeleton (`api/`, `core/`, `data/repositories/`, `models/`, `agents/`, `orchestration/`, `rag/`, `schemas/`, `services/`, `sources/`, `tools/`, `workflows/`) with `__init__.py` stubs under `backend/app/`
- [X] T004 [P] Configure Docker + docker-compose for local dev against Neon Postgres in `backend/Dockerfile`, `docker-compose.yml`
- [X] T005 [P] Configure GitHub Actions CI workflow in `.github/workflows/ci.yml`
- [X] T006 [P] Create eval-harness skeleton in `backend/evals/runner.py`, `backend/evals/cases/__init__.py`
- [X] T007 [P] Ratify project charter in `specs/charter.md`
- [X] T008 Set up SQLAlchemy 2.0 engine/session + Alembic migrations framework in `backend/app/data/repositories/db.py`, `backend/alembic.ini`, `backend/migrations/env.py`, `backend/migrations/script.py.mako`
- [X] T009 Create `users` table + first migration in `backend/app/models/user.py`, `backend/migrations/versions/fed0167c65c9_create_users_table.py`
- [X] T010 Implement JWT auth (signup/login/logout) in `backend/app/api/auth.py`, `backend/app/api/deps.py`, `backend/app/core/security.py`, `backend/app/core/config.py`, `backend/app/schemas/auth.py`
- [X] T011 [P] Auth + health + DB-connection tests in `backend/tests/test_auth.py`, `backend/tests/test_health.py`, `backend/tests/test_db_connection.py`

**Checkpoint**: Foundation complete and reused as-is — `api/auth.py`, `api/deps.py`, `core/config.py`, `core/security.py`, `data/repositories/db.py`, `models/user.py` are not touched again below.

---

## Phase 1: Profile + URL Match + Q&A (US1, US2, US3 — Priority P1) 🎯 MVP

**Goal**: A student can build a constraint-typed profile, match a specific scholarship by URL with an explainable, evidence-backed verdict, and ask grounded follow-up questions — delivering PRD AC-1 (spec.md SC-001).

### Phase 1 Foundational (shared prerequisites for US1/US2/US3)

**⚠️ Blocks US3 entirely (RAG/grounding) and is a prerequisite for US2's grounded extraction; run before story work.**

- [ ] T012 [P] Shared `ValueStatus`/`Confidence`/`FieldValue` schema types (constitution Principle I vocabulary) in `backend/app/schemas/common.py`
- [ ] T013 [P] Qdrant Cloud client wrapper in `backend/app/data/vectors/qdrant_client.py`
- [ ] T014 [P] RAG document loaders in `backend/app/rag/loaders.py`
- [ ] T015 [P] RAG chunking in `backend/app/rag/chunk.py`
- [ ] T016 [P] Embedding tool wrapper in `backend/app/tools/embed.py`
- [ ] T017 RAG embedding pipeline (ingests via T014/T015, embeds via T016) in `backend/app/rag/embed.py`
- [ ] T018 RAG retrieval over Qdrant (depends on T013) in `backend/app/rag/retrieve.py`
- [ ] T019 [P] `ground_check` tool (used by Q&A now; reused by CV/SOP generation in Phase 3) in `backend/app/tools/ground_check.py`
- [ ] T020 RAG grounding-check node (depends on T018, T019) in `backend/app/rag/grounding.py`

**Checkpoint**: RAG/grounding + vector store ready — US3 can now be built; US1/US2 do not depend on this subsection.

### Tests for User Story 1 — Profile (write first, must fail before implementation)

- [ ] T021 [P] [US1] Contract test: `GET/PUT /profile`, `POST /profile/criteria`, `POST /profile/import-cv` in `backend/tests/api/test_profile_api.py`
- [ ] T022 [P] [US1] Integration test covering spec.md US1 Acceptance Scenarios 1–4 (hard-constraint save, partial update, missing-field-not-silently-omitted, criterion reclassification) in `backend/tests/integration/test_profile_flow.py`
- [ ] T023 [P] [US1] Unit test: `missing_info` tracking + `profile_criteria.kind` validation (hard_constraint/soft_preference/exclusion) in `backend/tests/services/test_profile_service.py`

### Implementation for User Story 1

- [ ] T024 [US1] SQLAlchemy + Pydantic models for `profiles`, `education_records`, `test_scores`, `experience`, `profile_criteria`, `missing_info` in `backend/app/models/profile.py`
- [ ] T025 [US1] Alembic migration for profile tables (depends on T024) in `backend/migrations/versions/{rev}_create_profile_tables.py`
- [ ] T026 [US1] Profile repository, `user_id`-scoped (depends on T025) in `backend/app/data/repositories/profile_repo.py`
- [ ] T027 [US1] Profile service — CRUD, criteria classify/reclassify, missing-info computation (depends on T026) in `backend/app/services/profile.py`
- [ ] T028 [US1] Profile request/response schemas (depends on T024) in `backend/app/schemas/profile.py`
- [ ] T029 [P] [US1] `extract_profile` tool — CV-import field suggestions, not auto-saved (FR contract: suggestions only) in `backend/app/tools/extract_profile.py`
- [ ] T030 [US1] Profile API endpoints: `GET/PUT /profile`, `POST /profile/criteria`, `POST /profile/import-cv` (depends on T027, T028, T029) in `backend/app/api/profile.py`
- [ ] T031 [US1] Wire profile router into the app (depends on T030) in `backend/app/main.py`

**Checkpoint**: US1 independently functional and testable (T021–T023 pass) per spec.md's Independent Test.

### Tests for User Story 2 — Match a scholarship by URL (write first, must fail before implementation)

- [ ] T032 [P] [US2] Contract test: `POST /scholarships/match-url`, `POST /scholarships/{id}/match`, `GET /matches` in `backend/tests/api/test_matching_api.py`
- [ ] T033 [P] [US2] Integration test covering US2 Acceptance Scenarios 1–4 (full verdict with evidence, hard-fail overrides soft strength, unknown-not-assumed-pass/fail, unreachable-URL reported explicitly, never as "not eligible") in `backend/tests/integration/test_url_match_flow.py`
- [ ] T034 [US2] **Hard-constraint verdict-correctness test matrix** — fixed set of (known profile × known scholarship) fixture pairs → expected `eligibility_verdict`; constitution Principle II + spec.md SC-007: 100% of hard-constraint failures correctly reported as ineligible, zero overrides by soft-criteria strength; **build-breaking on failure, not a warning** — in `backend/tests/services/test_hard_constraints_matrix.py`
- [ ] T035 [P] [US2] Guardrail-violation tests: Matching-agent output contradicting a hard-constraint result is rejected; a matched/failed criterion lacking an evidence reference is rejected; the agent has no web/search/fetch tool bound — in `backend/tests/agents/test_matching_guardrails.py`
- [ ] T036 [P] [US2] Unit test: ranking never reorders past a hard-constraint failure (deterministic ranking formula, not agent-controlled) in `backend/tests/services/test_ranking.py`

### Implementation for User Story 2

- [ ] T037 [P] [US2] SQLAlchemy + Pydantic models: `scholarships`, `universities`, `programs`, `scholarship_fields` in `backend/app/models/scholarship.py`
- [ ] T038 [P] [US2] SQLAlchemy + Pydantic models: `requirements` in `backend/app/models/requirement.py`
- [ ] T039 [P] [US2] SQLAlchemy + Pydantic models: `funding_details` in `backend/app/models/funding.py`
- [ ] T040 [P] [US2] SQLAlchemy + Pydantic models: `matches` in `backend/app/models/match.py`
- [ ] T041 [US2] Alembic migration for scholarship/requirement/funding/match tables (depends on T037–T040) in `backend/migrations/versions/{rev}_create_scholarship_matching_tables.py`
- [ ] T042 [P] [US2] Scholarship repository (depends on T041) in `backend/app/data/repositories/scholarship_repo.py`
- [ ] T043 [P] [US2] Match repository, immutable append-only writes (depends on T041) in `backend/app/data/repositories/match_repo.py`
- [ ] T044 [P] [US2] `web_fetch` tool — official-page fetch with explicit failure reporting (never silently "not eligible") in `backend/app/tools/web_fetch.py`
- [ ] T045 [P] [US2] `extract_requirements` tool — LLM-assisted extraction of requirements/funding/deadline from a fetched page in `backend/app/tools/extract_requirements.py`
- [ ] T046 [US2] `hard_constraints` service — deterministic GPA/degree/nationality/deadline/mandatory-test evaluation (depends on T038, T039) in `backend/app/services/hard_constraints.py`
- [ ] T047 [US2] `ranking` service — deterministic ranking formula, agent supplies inputs only (depends on T046) in `backend/app/services/ranking.py`
- [ ] T048 [US2] `MatchVerdict`/`CriterionOutcome` schemas — fixed, schema-locked, no free-text verdict (depends on T040) in `backend/app/schemas/match.py`
- [ ] T049 [US2] `url_match` LangGraph workflow: fetch → extract → verify → `hard_constraints` → Matching agent → persist (depends on T044, T045, T046) in `backend/app/workflows/url_match/graph.py`
- [ ] T050 [US2] Matching agent — guardrailed: schema-locked output, evidence-required, no-guess, no web/fetch tools, hard-constraint input is read-only (depends on T046, T048) in `backend/app/agents/matching/agent.py`
- [ ] T051 [US2] URL-match API endpoint: `POST /scholarships/match-url` (depends on T049) in `backend/app/api/url_match.py`
- [ ] T052 [US2] Matching API endpoints: `POST /scholarships/{id}/match`, `GET /matches` (depends on T050, T047, T043) in `backend/app/api/matching.py`
- [ ] T053 [US2] Wire `url_match` + `matching` routers into the app (depends on T051, T052) in `backend/app/main.py`

**Checkpoint**: US2 independently functional; verdict-correctness matrix (T034) and guardrail tests (T035) green.

### Tests for User Story 3 — Grounded Q&A (write first, must fail before implementation)

- [ ] T054 [P] [US3] Contract test: `POST /scholarships/{scholarship_id}/qa` in `backend/tests/api/test_qa_api.py`
- [ ] T055 [P] [US3] Integration test covering US3 Acceptance Scenarios 1–4 (verified answer with citation, unknown-not-guessed, inferred-labeled-not-verified, unrelated question not answered as grounded fact) in `backend/tests/integration/test_qa_flow.py`
- [X] T056 [P] [US3] Q&A groundedness eval case (scored, added to eval harness — constitution Principle V) in `backend/evals/cases/qa_groundedness.py`

### Implementation for User Story 3

- [ ] T057 [US3] `QaAnswer` schema (no persisted table — data-model.md §5) in `backend/app/schemas/qa.py`
- [ ] T058 [US3] Research/Q&A agent — grounded via retrieve+grounding (T018/T020), labels verified/inferred/unknown, cites `source_url`/`last_verified_at` (depends on T057, T018, T020) in `backend/app/agents/research_qa/agent.py`
- [ ] T059 [US3] Q&A API endpoint: `POST /scholarships/{scholarship_id}/qa` (depends on T058) in `backend/app/api/qa.py`
- [ ] T060 [US3] Wire `qa` router into the app (depends on T059) in `backend/app/main.py`

**Checkpoint**: US3 independently functional.

### Phase 1 Integration (cross-story orchestration)

- [X] T061 Main Orchestrator — intent routing across profile/url-match/qa/matching; requests clarification rather than guessing on ambiguous/uncertain intent (FR-ROUTE-1..3) (depends on T030, T051, T052, T059) in `backend/app/orchestration/main_agent.py`
- [X] T062 [P] Integration test: ambiguous and multi-capability intent routing + clarification request (Edge Cases: "find and match, then draft my SOP") in `backend/tests/integration/test_main_agent_routing.py`
- [X] T063 Extend eval-harness config to run matching-correctness + qa-groundedness evals as a CI score-regression gate (depends on T034, T056) in `backend/evals/runner.py`, `.github/workflows/ci.yml`

**Phase 1 Checkpoint**: US1 + US2 + US3 all functional; hard-constraint verdict matrix and guardrail tests green; eval score-gate active. **MVP demoable — spec.md SC-001.**

---

## Phase 2: Discovery from Governed Sources (US4 — Priority P2)

**Goal**: A student without a specific scholarship finds ranked, source-attributed results from an approved Source Registry, with measured (never "complete") coverage — PRD AC-2, spec.md SC-002/SC-003.

### Tests for User Story 4 (write first, must fail before implementation)

- [ ] T064 [P] [US4] Contract test: `POST /discovery` in `backend/tests/api/test_discovery_api.py`
- [ ] T065 [P] [US4] Integration test covering US4 Acceptance Scenarios 1–5 (≥2 distinct source types with attribution, coverage summary never claims complete/100%, new source recorded as pending-not-authoritative, fetch failure logged as a gap not "no scholarships exist", conflicting sources surfaced unless the official>recent>reliable rule resolves them) in `backend/tests/integration/test_discovery_flow.py`
- [X] T066 [P] [US4] Source-registry compliance tests: non-`active` `source_id` blocks fetch; a `candidate_sources` row is never joined as authoritative before `approved` (constitution Principle IV, Development Workflow gate) in `backend/tests/sources/test_source_registry_compliance.py`
- [ ] T067 [P] [US4] Unit test: `conflict_resolution` policy (official > recent > reliable; unresolved ⇒ surfaced `conflicting`, never silently averaged) in `backend/tests/services/test_conflict_resolution.py`
- [ ] T068 [P] [US4] Unit test: `coverage` counts/gaps computation, `claims_complete_coverage` always `false` in `backend/tests/services/test_coverage.py`

### Implementation for User Story 4

- [X] T069 [US4] SQLAlchemy + Pydantic models: `source_registry`, `candidate_sources`, `source_fetch_log`, `scholarship_sources` in `backend/app/models/source.py`
- [X] T070 [US4] Alembic migration for source-registry tables (depends on T069) in `backend/migrations/versions/{rev}_create_source_registry_tables.py`
- [X] T071 [US4] Source repository (depends on T070) in `backend/app/data/repositories/source_repo.py`
- [ ] T072 [P] [US4] Bounded retry policy for source fetches in `backend/app/sources/retry_policy.py`
- [ ] T073 [US4] `api_connector` + `official_fetch` connector wrappers — reject any fetch whose `source_id` isn't `active` (depends on T071) in `backend/app/sources/connectors/api_connector.py`, `backend/app/sources/connectors/official_fetch.py`
- [ ] T074 [P] [US4] `official_fetch` tool, registry-governed (depends on T073) in `backend/app/tools/official_fetch.py`
- [ ] T075 [P] [US4] `search` tool — scoped to governed sources, not unrestricted web search (FR-DISC-2) in `backend/app/tools/search.py`
- [ ] T076 [P] [US4] `api_connector` tool in `backend/app/tools/api_connector.py`
- [ ] T077 [P] [US4] `classify` tool — rules + LLM fallback for fuzzy classification in `backend/app/tools/classify.py`
- [ ] T078 [US4] `source_registry` service — registry CRUD, active-only gating (depends on T071) in `backend/app/services/source_registry.py`
- [ ] T079 [P] [US4] `normalize` service in `backend/app/services/normalize.py`
- [ ] T080 [US4] `classify` service — dispatches to T077 (depends on T077) in `backend/app/services/classify.py`
- [ ] T081 [P] [US4] `dedup` service in `backend/app/services/dedup.py`
- [ ] T082 [P] [US4] `verification` service — freshness window, `lifecycle_status` transitions in `backend/app/services/verification.py`
- [ ] T083 [P] [US4] `conflict_resolution` service — official > recent > reliable in `backend/app/services/conflict_resolution.py`
- [ ] T084 [US4] `coverage` service — measured summary (checked/failed/gaps) in `backend/app/services/coverage.py`
- [ ] T085 [P] [US4] `rerank` tool, optional/flagged in `backend/app/tools/rerank.py`
- [ ] T086 [US4] `source_validate` workflow — human-approval-gated candidate-source promotion (depends on T078) in `backend/app/workflows/source_validate/graph.py`
- [ ] T087 [US4] `ingestion` workflow — extract → normalize → dedup → verify → classify pipeline (depends on T079, T080, T081, T082, T083) in `backend/app/workflows/ingestion/graph.py`
- [ ] T088 [US4] `DiscoveryResult`/`CoverageSummary` schemas (depends on T069) in `backend/app/schemas/discovery.py`
- [ ] T089 [US4] Discovery agent — governed sources only, multi-dimension query support (depends on T087, T084, T088) in `backend/app/agents/discovery/agent.py`
- [ ] T090 [US4] Discovery API endpoint: `POST /discovery` (depends on T089) in `backend/app/api/discovery.py`
- [ ] T091 [US4] Extend orchestrator to route discovery intent (depends on T090) in `backend/app/orchestration/main_agent.py`
- [ ] T092 [US4] Wire `discovery` router into the app (depends on T090) in `backend/app/main.py`

**Phase 2 Checkpoint**: US4 independently functional; source-registry compliance + conflict-resolution tests green — spec.md SC-002/SC-003 demoable.

---

## Phase 3: Documents, CV, and SOP (US5 — Priority P3)

**Goal**: A student uploads documents against a selected scholarship's requirements and receives a requirement-aware CV/SOP with 100% factual claims traceable to profile/documents — PRD AC-3, spec.md SC-004.

### Tests for User Story 5 (write first, must fail before implementation)

- [ ] T093 [P] [US5] Contract test: `POST /applications` (minimal create — see Resolution note 3), `POST /applications/{id}/documents` in `backend/tests/api/test_documents_api.py`
- [ ] T094 [P] [US5] Contract test: `POST /applications/{id}/generate/cv`, `POST /applications/{id}/generate/sop` in `backend/tests/api/test_generation_api.py`
- [ ] T095 [P] [US5] Integration test covering US5 Acceptance Scenarios 1–5 (transcript parsed/associated/requirement-satisfied, GPA inconsistency flagged not silently resolved, Europass/specified-questions format compliance, every claim traceable, gap reported not invented) in `backend/tests/integration/test_document_generation_flow.py`
- [ ] T096 [P] [US5] Anti-hallucination eval case: generated CV/SOP claims are 100% traceable (SC-004) in `backend/evals/cases/generation_groundedness.py`
- [ ] T097 [P] [US5] Unit test: `ground_check` blocks generation on an untraceable claim in `backend/tests/tools/test_ground_check.py`

### Implementation for User Story 5

- [ ] T098 [US5] Minimal `applications` model — `id, user_id FK, scholarship_id FK, status, created_at` only; `tasks`/`submission_approvals` added in Phase 4 (Resolution note 3) in `backend/app/models/application.py`
- [ ] T099 [US5] SQLAlchemy + Pydantic models: `application_documents`, `generated_documents` in `backend/app/models/document.py`
- [ ] T100 [US5] Alembic migration for `applications`(minimal) + `application_documents` + `generated_documents` (depends on T098, T099) in `backend/migrations/versions/{rev}_create_document_tables.py`
- [ ] T101 [US5] Application repository — minimal create/get; extended in Phase 4 (depends on T100) in `backend/app/data/repositories/application_repo.py`
- [ ] T102 [US5] Document repository (depends on T100) in `backend/app/data/repositories/document_repo.py`
- [ ] T103 [P] [US5] Local-filesystem object-storage abstraction (S3-compatible interface later) in `backend/app/data/files/storage.py`
- [ ] T104 [P] [US5] `pdf_parse` tool in `backend/app/tools/pdf_parse.py`
- [ ] T105 [US5] `doc_pipeline` workflow — accept upload → parse → associate → compare against target requirement → flag inconsistencies (depends on T102, T103, T104) in `backend/app/workflows/doc_pipeline/graph.py`
- [ ] T106 [US5] `UploadedDocument`/`GeneratedDocument`/`DocumentType` schemas + minimal `Application` schema (depends on T099) in `backend/app/schemas/document.py`
- [ ] T107 [US5] Minimal application-create endpoint: `POST /applications` (depends on T101) in `backend/app/api/application.py`
- [ ] T108 [US5] Documents API endpoint: `POST /applications/{id}/documents`, `422` on parse failure — user asked to retry, never silently ignored (depends on T105, T106) in `backend/app/api/documents.py`
- [ ] T109 [US5] `cv_gen` workflow — requirement-aware, format-specific (e.g. Europass), `ground_check`-gated (depends on T019, T020, T102) in `backend/app/workflows/cv_gen/graph.py`
- [ ] T110 [US5] `sop_gen` workflow — answers scholarship-specific questions, `ground_check`-gated (depends on T019, T020, T102) in `backend/app/workflows/sop_gen/graph.py`
- [ ] T111 [US5] Generation API endpoints: `POST /applications/{id}/generate/cv`, `/generate/sop`, `409` on an information gap — reported, never filled with invented content (depends on T109, T110) in `backend/app/api/generation.py`
- [ ] T112 [US5] Wire minimal `application` + `documents` + `generation` routers into the app (depends on T107, T108, T111) in `backend/app/main.py`

**Phase 3 Checkpoint**: US5 independently functional; zero-fabrication eval (T096) green — spec.md SC-004 demoable.

---

## Phase 4: Application Planning + Human-Approved Assistant (US6 — Priority P4)

**Goal**: A student gets a labeled readiness checklist and step-by-step or deterministic-mode assistance, with zero materials transmitted anywhere without an explicit, per-submission approval — PRD AC-4, spec.md SC-005/SC-006.

### Tests for User Story 6 (write first, must fail before implementation)

- [ ] T113 [P] [US6] Contract test: `GET /applications` (tracker), `POST /applications/{id}/plan` in `backend/tests/api/test_application_api.py`
- [ ] T114 [P] [US6] Contract test: `POST /applications/{id}/assistant/next-step`, `POST /applications/{id}/submission-approvals` in `backend/tests/api/test_application_assistant_api.py`
- [ ] T115 [P] [US6] Integration test covering US6 Acceptance Scenarios 1–5 (every checklist item carries exactly one of the six readiness labels, missing-vs-AI-can-generate distinguished, nothing transmitted before explicit approval, an earlier approval does not carry forward to a new submission, agentic mode and deterministic mode produce identical checklists for the same inputs) in `backend/tests/integration/test_application_planning_flow.py`
- [ ] T116 [P] [US6] Submission-approval scoping test: an approval for one `submission_scope` MUST NOT authorize a different/later submission (constitution Principle III, FR-APP-3) in `backend/tests/services/test_submission_approval_scope.py`
- [ ] T117 [P] [US6] Static/contract test asserting no live-external-send endpoint or code path exists anywhere in the API surface (FR-APP-4) in `backend/tests/api/test_no_live_submission.py`

### Implementation for User Story 6

- [ ] T118 [US6] SQLAlchemy + Pydantic models: `tasks`, `submission_approvals` (extends `applications` from Phase 3) in `backend/app/models/application.py`
- [ ] T119 [US6] Alembic migration for `tasks` + `submission_approvals` (depends on T118) in `backend/migrations/versions/{rev}_create_application_planning_tables.py`
- [ ] T120 [US6] Extend application repository with plan/tracker/readiness queries (depends on T119) in `backend/app/data/repositories/application_repo.py`
- [ ] T121 [US6] `readiness` service — recomputes `readiness_label` from document/generation state, never hand-edited (depends on T120) in `backend/app/services/readiness.py`
- [ ] T122 [US6] `app_plan` workflow — verified requirements → six-label checklist; single workflow, two entry points so agentic and deterministic modes are provably identical (depends on T121) in `backend/app/workflows/app_plan/graph.py`
- [ ] T123 [US6] `submit_prep` workflow — stops at `[APPROVAL GATE]`/`[FINAL APPROVAL GATE]`; no live-send code path exists (depends on T120) in `backend/app/workflows/submit_prep/graph.py`
- [ ] T124 [US6] Application/Submission agent — drafts/assembles/stages materials only; wraps `app_plan`/`submit_prep`; exposes step-by-step and deterministic entry points (depends on T122, T123) in `backend/app/agents/application/agent.py`
- [ ] T125 [US6] `ApplicationPlan`/`ChecklistItem`/`ReadinessLabel`/`AssistantStepResult` schemas (depends on T118) in `backend/app/schemas/plan.py`
- [ ] T126 [US6] `GET /applications` (tracker) + `POST /applications/{id}/plan` endpoints (depends on T122, T125) in `backend/app/api/application.py`
- [ ] T127 [US6] Assistant + submission-approval endpoints: `POST /applications/{id}/assistant/next-step`, `POST /applications/{id}/submission-approvals` (append-only; never edited/reused) (depends on T124, T123) in `backend/app/api/application.py`
- [ ] T128 [US6] Extend orchestrator to route application-planning/assistant intents (depends on T126, T127) in `backend/app/orchestration/main_agent.py`
- [ ] T129 [US6] Wire the remaining `application` endpoints into the app — minimal create was already wired in Phase 3 (depends on T126, T127) in `backend/app/main.py`

**Phase 4 Checkpoint**: US6 independently functional; submission-approval scoping (T116) and no-live-send (T117) tests green — spec.md SC-005/SC-006 demoable. `workflows/source_monitor/` remains deliberately unbuilt (Resolution note 4).

---

## Final Phase: Polish & Cross-Cutting Concerns

**Purpose**: Whole-project verification once all four phases are complete.

- [ ] T130 [P] Run `specs/001-scholarship-mvp/quickstart.md` validation against all Phase 1–4 endpoints
- [ ] T131 [P] Code-review checklist pass: confirm no LLM code path reaches into `hard_constraints`/`ranking` (Principle II) and no code path calls a live external submission API (Principle III) — constitution "Development Workflow & Quality Gates"
- [ ] T132 [P] Security hardening pass: confirm every repository query is `user_id`-scoped (FR-AUTH-2) across `profile_repo`, `scholarship_repo`, `match_repo`, `source_repo`, `document_repo`, `application_repo`
- [ ] T133 Regenerate/verify `specs/001-scholarship-mvp/contracts/openapi.yaml` matches implemented FastAPI routes (drift check)
- [ ] T134 Full eval-suite run + CI score-regression gate verification across matching-correctness, qa-groundedness, generation-groundedness, and source-registry-compliance cases in `backend/evals/runner.py`

---

## Dependencies & Execution Order

### Phase dependencies

- **Phase 0 (Foundation)**: COMPLETE. Everything below reuses it unchanged.
- **Phase 1 Foundational (T012–T020)**: Depends on Phase 0. Blocks US3 entirely; US1 and US2 do not depend on it.
- **US1, US2, US3** (within Phase 1): Touch disjoint files and can be built in parallel. Runtime, not build-time, coupling exists — US2's matching step and US3's Q&A operate against a profile/scholarship; for isolated testing per spec.md's Independent Test criteria, seed fixtures directly via the repositories rather than routing through another story's API.
- **Phase 1 Integration (T061–T063)**: Depends on all three stories' endpoints existing (T030, T051, T052, T059).
- **Phase 2 (US4)**: Depends on Phase 1's scholarship tables (T037–T041) — discovery writes into the same `scholarships`/`requirements`/`funding_details` rows URL-match writes into.
- **Phase 3 (US5)**: Depends on Phase 1 (profile for grounding, `requirements` to drive document types) and introduces the minimal `applications` table Phase 4 extends.
- **Phase 4 (US6)**: Depends on Phase 3's minimal `applications` table (T098–T101) and draws on Phase 1–3 outputs (matching, documents, generation) for readiness computation.

### Within each user story

Tests written and failing → models → migration → repository → services → tools → workflow/agent → API endpoint → wire into `main.py`, matching the TDD + "models→services→endpoints" ordering requested.

---

## Parallel Example (one per phase)

```bash
# Phase 1 foundational — independent files:
Task: "Shared ValueStatus/Confidence schema in backend/app/schemas/common.py"
Task: "Qdrant client wrapper in backend/app/data/vectors/qdrant_client.py"
Task: "RAG loaders in backend/app/rag/loaders.py"

# Phase 1, US2 models — independent files:
Task: "scholarships/universities/programs/scholarship_fields models in backend/app/models/scholarship.py"
Task: "requirements model in backend/app/models/requirement.py"
Task: "funding_details model in backend/app/models/funding.py"
Task: "matches model in backend/app/models/match.py"

# Phase 2 tools — independent files:
Task: "search tool in backend/app/tools/search.py"
Task: "api_connector tool in backend/app/tools/api_connector.py"
Task: "classify tool in backend/app/tools/classify.py"
```

---

## Implementation Strategy

Follow plan.md's blueprint phase-ordering exactly — not a generic incremental-team strategy:

1. Phase 0 — already done.
2. Phase 1 (US1+US2+US3) — **STOP and VALIDATE**: hard-constraint matrix + guardrail tests green, eval score-gate active. This is the MVP (spec.md SC-001).
3. Phase 2 (US4) — validate source-registry compliance + coverage-never-complete before moving on.
4. Phase 3 (US5) — validate zero-fabrication eval (SC-004) before moving on.
5. Phase 4 (US6) — validate submission-approval scoping + no-live-send before calling the MVP complete.
6. Final Phase — whole-project polish/verification.

Each phase is independently demoable per spec.md's per-story Independent Test criteria; do not skip a phase's checkpoint validation before starting the next.

---

## Notes

- `[P]` tasks = different files, no dependency on an incomplete task.
- `[Story]` label maps every user-story-phase task to US1–US6 for traceability; Phase 0, Phase 1 Foundational, Phase 1 Integration, and the Final Phase carry no story label by template convention.
- Tests are mandatory here (see "Tests" note above) — write them first and confirm they fail before implementing.
- Commit after each task or logical group; stop at every phase checkpoint to validate independently before proceeding.
- Avoid: vague tasks, same-file conflicts marked `[P]`, and cross-story dependencies that would break a story's independent testability.
