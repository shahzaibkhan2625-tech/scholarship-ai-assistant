# Implementation Plan: Scholarship AI Assistant — MVP Core Platform

**Branch**: `001-scholarship-mvp` | **Date**: 2026-09-06 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/001-scholarship-mvp/spec.md`
**Authoritative architecture source**: `specs/scholarship-ai-assistant-master-blueprint-v1.md` (v1.6) + `.specify/memory/constitution.md` (v1.0.0) + `specs/charter.md`

## Summary

Build the MVP backend for the Scholarship AI Assistant as a **Python 3.12 / FastAPI modular monolith** (`backend/app/`), reusing the already-implemented Phase 0 foundation (auth, `users` table, SQLAlchemy/Alembic DB layer, Docker, CI, eval-harness skeleton) without redesigning it. The system implements **exactly five agents** (Main Orchestrator, Research/Q&A, Discovery, Application/Submission, Matching-guardrailed) via LangGraph + LangChain + the OpenAI Agents SDK, with document processing, CV/SOP generation, application planning, and profile management built as **deterministic workflows/services** (LLM tools only for fuzzy sub-steps) per the blueprint's Architecture Decision Matrix (§9) — never as agents. PostgreSQL (Neon) holds all structured/business/provenance data; Qdrant Cloud holds retrieval knowledge only and is never the source of truth for fresh, time-sensitive facts. Hard-constraint eligibility is computed by a deterministic `hard_constraints` service and passed **read-only** into the guardrailed Matching agent, which is schema-locked, evidence-required, no-guess, and has no web/fetch tools; ranking stays in code. Every scholarship field carries a value-status + provenance; Q&A and matching outputs carry Verified/Inferred/Unknown labels. Only Source-Registry-approved sources are authoritative; newly observed sources land as `pending` candidates. No live external submission ships in MVP — the Application/Submission agent drafts, assembles, and stages materials behind per-submission human approval gates only.

Implementation is **sequenced by the blueprint's phase ordering** (§22), mapped onto this spec's user-story priorities:

| Blueprint phase | Spec user stories covered | Priority |
|---|---|---|
| Phase 1 — Profile + URL Match + Q&A | US1 (profile), US2 (URL match), US3 (grounded Q&A) | P1 |
| Phase 2 — Governed multi-source Discovery | US4 (discovery) | P2 |
| Phase 3 — Documents + CV/SOP | US5 (documents, CV/SOP generation) | P3 |
| Phase 4 — Planner + Tracker + Application/Submission Agent | US6 (application planning, assisted submission staging) | P4 |

This plan covers the full feature's data model and API contracts (all capabilities, per §5.1) so `/sp.tasks` can decompose work phase-by-phase without re-deriving architecture; **implementation itself proceeds Phase 1 first**, per blueprint ordering rationale (profile is prerequisite for everything; URL-match + Q&A deliver real value with the least infrastructure). Frontend (React + TypeScript) is explicitly a later phase and out of scope for this plan's contracts beyond the OpenAPI surface they will consume.

## Technical Context

**Language/Version**: Python 3.12 (backend only in this plan; React + TypeScript frontend is a later phase per blueprint §5/§21 and the user's explicit instruction — not designed here)
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2.0 + Alembic, LangGraph, LangChain, OpenAI Agents SDK, MCP (client SDK for tool access), qdrant-client, python-jose + passlib/bcrypt (existing, Phase 0), pydantic-settings (existing)
**Storage**: PostgreSQL via Neon (structured facts, provenance, registry, applications) + Qdrant Cloud (semantic retrieval only — never source of truth for fresh facts) + local filesystem for uploaded/generated files in MVP (object-storage abstraction, S3-compatible interface later per blueprint A6)
**Testing**: pytest (existing, Phase 0) — unit, API, agent tool-call, RAG/retrieval, matching verdict-correctness + guardrail-violation, document, anti-hallucination, source-governance, connector-contract, ingestion, failure/retry, coverage, response-schema, security (per constitution Principle V and blueprint §25); plus a **scored eval suite** (`backend/evals/`, existing skeleton) gated in CI as a score-regression check (constitution Principle V, blueprint §25.1)
**Target Platform**: Linux containers (Docker Compose locally; a minimal cloud runtime — containerized backend + public endpoint + managed object storage — targeted for end of Phase 2 per blueprint §22, provider TBD — ⚠️ NOT YET DEFINED, non-blocking for Phase 1)
**Project Type**: Backend-only modular monolith for this plan (web app once frontend lands in a later phase)
**Performance Goals**: Not numerically specified by the PRD/spec for MVP (single-user personal tool); no NEEDS CLARIFICATION — deferred to Production phase per blueprint §26 (SaaS-scale SLOs are explicitly out of MVP scope)
**Constraints**: Hard-constraint matching MUST be deterministic and MUST NOT be overridable by the LLM (constitution Principle II); Matching agent MUST have no web/search/fetch tool access; no live external submission (constitution Principle III); Discovery/Research-QA MUST only treat Source-Registry `active` sources as authoritative (constitution Principle IV); every scholarship field MUST carry `value_status` + `confidence` + provenance (constitution Principle I)
**Scale/Scope**: Single-user MVP (owner's personal use per blueprint §1), `user_id`-scoped tables from day one so SaaS isolation is a later WHERE-clause guarantee, not a migration; seed Source Registry limited to "a few carefully researched countries/source types" (blueprint A11) — exact seed list is operational config, not architecture, and is not enumerated here (spec Assumptions)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design (see "Post-Design Constitution Check" below).*

| Principle | Check | Result |
|---|---|---|
| I. No Fabrication — Grounded Claims Only | Data model (Phase 1 output) gives every scholarship field/requirement/funding value a `value_status` (`known/unknown/not_applicable/conditional/conflicting`) + `confidence` (`verified/inferred/unknown`) + provenance; Q&A and matching schemas carry the same labels; `ground_check` nodes gate CV/SOP generation; missing profile fields are stored via `missing_info`, never defaulted | PASS |
| II. Deterministic Hard-Constraint Matching | `hard_constraints` service computes GPA/degree/nationality/deadline/mandatory-test results deterministically **before** the Matching agent runs; passed in read-only; Matching agent output is schema-locked (Pydantic `MatchVerdict`), evidence-required, no-guess, has no web/fetch tools; ranking formula lives in the `ranking` service, never the agent; verdict-correctness test matrix + guardrail-violation build-break planned for Phase 1 CI | PASS |
| III. Human Approval Before Any Live External Submission | `submit_prep` workflow and Application/Submission agent stop at `[APPROVAL GATE]`/`[FINAL APPROVAL GATE]`; `SubmissionApproval` is scoped to one specific submission event (never reused); no live-send code path exists in this plan (Phase 6, out of MVP) | PASS |
| IV. Registry-Approved Sources Only | `source_registry` + `candidate_sources` tables; connectors resolve a `source_id` and reject any fetch against a non-`active` source; new sources land `pending` and require the `source_validate` human-approval-gated workflow; conflict resolution follows the deterministic official>recent>reliable policy, else surfaced `conflicting` | PASS |
| V. Spec-Driven Development, Tests + Evals Gated in CI | This plan follows `spec.md`; `tasks.md` follows this plan (not created here); Phase 0's eval-harness skeleton + CI score-gate (`backend/evals/`, `.github/workflows/ci.yml`) is reused and extended, not rebuilt, starting with real matching-correctness + Q&A-groundedness evals in Phase 1 | PASS |
| Agent Architecture constraint (exactly 5 agents; doc/CV-SOP/planning/profile as workflows/services) | This plan introduces zero new agents and zero agents beyond the five named in `specs/charter.md`; document processing, CV/SOP generation, application planning, and profile management are designed as workflows/services per §9's Architecture Decision Matrix, matching the constitution's explicit classification | PASS |
| Technology stack constraint | Stack matches the constitution's list exactly (FastAPI/Python 3.12, PostgreSQL/Neon, Qdrant Cloud, LangGraph+LangChain+OpenAI Agents SDK, Docker+GitHub Actions, React+TS deferred) — no substitutions proposed | PASS |

**No violations.** No new architecturally-significant decision is introduced by this plan beyond what the constitution and blueprint have already ratified — the plan operationalizes existing, already-decided architecture into a concrete data model and API contracts. **No ADR is triggered.** Complexity Tracking table is therefore empty (see below).

## Project Structure

### Documentation (this feature)

```text
specs/001-scholarship-mvp/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md         # Phase 1 output
├── quickstart.md         # Phase 1 output
├── contracts/            # Phase 1 output (OpenAPI, per capability)
│   └── openapi.yaml
├── checklists/
│   └── requirements.md   # already exists (from /sp.specify)
└── tasks.md              # Phase 2 output — NOT created by this command
```

### Source Code (repository root)

Backend-only modular monolith (matches blueprint §6 exactly; Phase 0 items already exist and are reused unchanged):

```text
backend/
  app/
    api/                      # FastAPI routers — thin: validation + auth + dispatch only
      auth.py                 # existing (Phase 0) — reused as-is
      deps.py                 # existing (Phase 0) — reused as-is
      profile.py               # NEW — Phase 1
      url_match.py             # NEW — Phase 1
      qa.py                     # NEW — Phase 1
      discovery.py             # NEW — Phase 2
      matching.py               # NEW — Phase 1 (match-my-profile endpoint)
      documents.py              # NEW — Phase 3
      generation.py             # NEW — Phase 3 (cv/sop)
      application.py             # NEW — Phase 4 (planner/assistant/tracker/submission-approval)
    orchestration/             # Main Agent: intent routing, handoff, response composition
      main_agent.py             # NEW — Phase 1 (minimal routing needed once >1 capability exists)
    agents/                    # OpenAI Agents SDK — exactly 5 agents total across phases
      research_qa/              # NEW — Phase 1
      discovery/                 # NEW — Phase 2
      application/                # NEW — Phase 4
      matching/                    # NEW — Phase 1 (guardrailed; guardrails a-f)
    workflows/                  # LangGraph — stateful, checkpointed, HITL-capable
      url_match/                 # NEW — Phase 1
      ingestion/                  # NEW — Phase 2 (data-quality pipeline)
      source_validate/            # NEW — Phase 2 (candidate-source approval gate)
      doc_pipeline/                # NEW — Phase 3
      cv_gen/                       # NEW — Phase 3
      sop_gen/                       # NEW — Phase 3
      app_plan/                       # NEW — Phase 4
      submit_prep/                     # NEW — Phase 4 (approval gates; no live send)
      source_monitor/                   # designed only, NOT built in MVP (Phase 5)
    services/                    # deterministic business logic — no LLM calls unless noted
      profile.py                 # NEW — Phase 1
      hard_constraints.py         # NEW — Phase 1 (deterministic pre-step for Matching agent)
      ranking.py                   # NEW — Phase 1 (deterministic ranking formula)
      auth.py                       # existing (Phase 0) — reused
      source_registry.py             # NEW — Phase 2
      coverage.py                     # NEW — Phase 2 (basic counts + gaps)
      normalize.py                     # NEW — Phase 2
      classify.py                       # NEW — Phase 2 (rules + LLM tool for fuzzy)
      dedup.py                           # NEW — Phase 2
      verification.py                     # NEW — Phase 2
      conflict_resolution.py               # NEW — Phase 2
      readiness.py                          # NEW — Phase 4
    sources/                      # connectors + registry access + fetch-log + retry policy
      connectors/                  # api_connector, official_fetch wrappers — NEW Phase 2
      retry_policy.py                # NEW — Phase 2
    tools/                          # external-call surface only; typed, validated outputs
      web_fetch.py                   # NEW — Phase 1 (used by url_match)
      official_fetch.py               # NEW — Phase 2 (registry-governed)
      search.py                        # NEW — Phase 2
      api_connector.py                  # NEW — Phase 2
      pdf_parse.py                       # NEW — Phase 3
      extract_requirements.py             # NEW — Phase 1 (used by url_match)
      extract_profile.py                   # NEW — Phase 1 (CV-import)
      classify.py                            # NEW — Phase 2
      embed.py                                # NEW — Phase 1 (RAG ingestion)
      rerank.py                                # NEW — Phase 2, optional/flagged
      ground_check.py                           # NEW — Phase 1 (Q&A + CV/SOP)
    schemas/                        # intent-specific structured response schemas (§34)
      profile.py, match.py, qa.py, discovery.py, plan.py, document.py   # NEW — per phase
    rag/                             # loaders, chunk, metadata, embed, retrieve, rerank, grounding
      loaders.py, chunk.py, embed.py, retrieve.py, grounding.py           # NEW — Phase 1
    data/
      repositories/
        db.py                        # existing (Phase 0) — reused as-is
        profile_repo.py, scholarship_repo.py, match_repo.py, ...          # NEW — per phase, Postgres-only SQL surface
      vectors/
        qdrant_client.py               # NEW — Phase 1
      files/
        storage.py                      # NEW — Phase 3 (local FS now, S3-compatible interface later)
    models/                          # Pydantic (API) + ORM (DB) + domain models
      user.py                        # existing (Phase 0) — reused as-is
      profile.py, scholarship.py, requirement.py, funding.py, match.py,
      source.py, application.py, document.py                              # NEW — per phase
    core/
      config.py, security.py          # existing (Phase 0) — reused as-is
  tests/
    test_auth.py, test_db_connection.py, test_health.py                  # existing (Phase 0)
    services/, agents/, workflows/, tools/, rag/, api/                     # NEW — per phase, mirrors app/
  evals/
    runner.py                        # existing (Phase 0 skeleton) — extended, not replaced
    cases/                            # real cases added starting Phase 1 (matching-correctness, qa-groundedness)
  migrations/
    versions/
      fed0167c65c9_create_users_table.py   # existing (Phase 0)
      ...                                    # NEW migrations per phase (profiles, scholarships, source_registry, ...)
```

**Structure Decision**: Reuse the existing `backend/app/` modular-monolith skeleton unchanged (Phase 0: `api/auth.py`, `api/deps.py`, `core/config.py`, `core/security.py`, `data/repositories/db.py`, `models/user.py`, plus the already-present empty package stubs for `agents/`, `orchestration/`, `rag/`, `schemas/`, `services/`, `sources/`, `tools/`, `workflows/`). New modules populate those existing empty packages rather than restructuring them. No new top-level project (frontend, mobile) is introduced by this plan.

## Post-Design Constitution Check

*Re-run after Phase 1 design (data-model.md, contracts/, quickstart.md).*

| Principle | Design-time verification | Result |
|---|---|---|
| I. No Fabrication | `data-model.md` gives `scholarship_fields`, `requirements`, `funding_details` a `value_status` + `confidence` + `source_id`/`evidence_snippet` column set; `contracts/openapi.yaml` Q&A and match-URL responses require `evidence`, `confidence`/`verification_status`, `last_verified_at` fields — schema enforces the guarantee, not just prose | PASS |
| II. Deterministic Hard-Constraint Matching | `data-model.md`'s `profile_criteria.kind` (`hard_constraint/soft_preference/exclusion`) feeds `hard_constraints` service; `contracts/openapi.yaml` `MatchVerdict` schema is fixed/closed (no free-text verdict field) and separates `hard_constraints[]` (pass/fail/unknown) from `soft_preferences[]` and `exclusions_triggered[]` | PASS |
| III. Human Approval Before Submission | `data-model.md`'s `submission_approvals` table is keyed to one `application_id` + submission event, not reusable; `contracts/openapi.yaml`'s `POST /applications/{id}/submission-approvals` endpoint requires an explicit per-call approval record; no submit/send endpoint exists in the contract | PASS |
| IV. Registry-Approved Sources | `data-model.md`'s `source_registry.status` (`active/pending/disabled`... ) gate is referenced by every scholarship-source FK; `contracts/openapi.yaml` discovery/url-match responses carry `source_id` + `official_status` so a client can distinguish authoritative from non-authoritative content | PASS |
| V. Spec-Driven, Tests+Evals in CI | `quickstart.md` documents how to exercise each Phase-1 acceptance scenario against the contracts and where the corresponding eval case lives | PASS |

No violations surfaced during design. Complexity Tracking remains empty.

## Complexity Tracking

*No entries — Constitution Check passed with no violations at either gate.*
