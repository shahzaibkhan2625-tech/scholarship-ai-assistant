# Implementation Plan: Source Monitoring, Coverage Analytics & Production Hardening

**Branch**: `002-source-monitoring-hardening` | **Date**: 2026-10-03 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/002-source-monitoring-hardening/spec.md`
**Authoritative architecture source**: `specs/scholarship-ai-assistant-master-blueprint-v1.md` §22 Phase 5 / §30.4 / §33 / §35, `specs/scholarship-ai-assistant-prd-v1.md` (FR-DISC-4, FR-VERIFY-5, FR-COV-1, NFR-REL-1..3/OD-8), `.specify/memory/constitution.md` (v1.0.0), and `specs/001-scholarship-mvp/` (plan.md, data-model.md, contracts/openapi.yaml) as the inherited baseline this feature extends without modifying.

## Summary

Extend the existing MVP backend (unchanged modules reused as-is) with Phase 5 continuous-monitoring, coverage, lifecycle, and production-hardening capability. `source_monitor` is a **new deterministic LangGraph workflow** (`backend/app/workflows/source_monitor/`) — not a new agent — that reuses the existing `official_fetch` connector, the existing `ingestion` workflow, and the existing deterministic matching path unchanged; a new in-process scheduler (APScheduler) triggers it on a recurring interval, guarded by a Postgres advisory lock so a source is never processed by two overlapping runs (FR-MON-6). Source health and coverage analytics are additive read paths over the already-existing `source_registry`/`source_fetch_log` tables plus two small new columns (`listing_page_url`, `fetched_url`) — no existing 001 table is altered or dropped. Scholarship lifecycle state (`lifecycle_status`) already exists in 001 as a single closed enum column; 002 adds the transition **logic**, not new schema. Alerts ship as a new `alerts`/`alert_preferences` pair behind a channel-abstraction (`NotificationChannel` Protocol, mirroring the existing `StorageBackend` Protocol pattern), with in-app (DB row) and email (stdlib `smtplib`, zero new dependency) as the two shipped channels. Candidate-source validation composes existing extraction/fetch tools into a new, purely advisory `candidate_source_validations` history table; it never promotes a candidate (human approval via the existing, unchanged `source_repo.approve_candidate_source` remains the sole gate). Production hardening (NFR-HARDEN-1..8) is satisfied mostly by reusing what's already there (existing retry policy, Neon's managed backups, `beautifulsoup4`-based sanitization, `.env`/`pydantic-settings` secrets) plus four narrowly-justified additions: **APScheduler** (recurring trigger — nothing in-stack schedules anything), **Caddy** (TLS termination sidecar — nothing in-stack terminates TLS), **`prometheus-client`** (metrics exposition — nothing in-stack emits metrics), and **`pip-audit`** (dev/CI-only dependency scanning — nothing in-stack scans dependencies). Full justification for each is in `research.md`.

One correction surfaced during research and reported up front: 001's plan.md/constitution describe the agent stack as including "the OpenAI Agents SDK," but `backend/pyproject.toml` has no such dependency — the actual, installed, imported LLM provider is Google Gemini (`google-genai` + `langchain-google-genai`). This plan reuses the real stack (verified by reading `backend/` directly) rather than the aspirational label; it does not change any decision in this plan, since no part of `source_monitor` calls an LLM at all (FR-MON-5, Principle II).

## Technical Context

**Language/Version**: Python 3.12 (unchanged from 001 — backend-only; no frontend work in this plan)
**Primary Dependencies**: Everything 001 already uses, unchanged (FastAPI, Pydantic v2, SQLAlchemy 2.0 + Alembic, LangGraph, LangChain, Google Gemini via `google-genai`/`langchain-google-genai` — see "stack-reuse correction" above, `qdrant-client`, `beautifulsoup4`, `httpx`). New, narrowly-scoped additions (each justified in `research.md` Decision 1 / Decision 7): **APScheduler** (in-process recurring scheduler), **`prometheus-client`** (metrics exposition), **`pip-audit`** (dev-only, CI dependency scanning). **Caddy** is a new infra component (reverse-proxy container), not a Python dependency.
**Storage**: PostgreSQL via Neon (unchanged — additive columns/tables only, see `data-model.md`) + Qdrant Cloud (unchanged, not touched by this feature) + local filesystem for documents, now mounted as a persistent Podman volume so it survives a redeploy (NFR-HARDEN-6) — no code change to `app/data/files/storage.py`, which already abstracts this behind `StorageBackend`.
**Testing**: pytest (unchanged, extended with new test modules for `source_monitor`, `services/lifecycle.py`, `services/alerts.py`, `services/candidate_validation.py`, `services/retention.py`, the rate-limit middleware, and FR-MON-6's concurrency guard) + the existing scored eval suite (`backend/evals/`) — no monitoring/alert/health logic is agentic, so no *new* eval cases are required by this feature (only tests); existing matching-correctness/Q&A-groundedness evals are unaffected since this feature calls the same deterministic matching code path.
**Target Platform**: Linux containers via Podman on the existing Oracle VM (`docker-compose.podman.yml`, `network_mode: host`), now with a `caddy` sidecar service added to that same compose file for TLS termination; still a single logical deployment unit (two containers: `backend` + `caddy`), not a new distributed system.
**Project Type**: Backend-only modular monolith (unchanged from 001 — this feature adds no frontend surface; `/metrics`, `/coverage`, `/alerts`, `/monitoring/*` are API-only).
**Performance Goals**: Not numerically specified by the spec (consistent with 001 — single/small-multi-user tool, not SaaS-scale). `RATE_LIMIT_PER_MINUTE` and `MONITORING_INTERVAL_MINUTES` are operational config (spec Assumptions), not architecture.
**Constraints**: Same non-negotiables as 001, reaffirmed for this feature: hard-constraint matching stays deterministic and is never recomputed by an LLM inside `source_monitor`'s rematch step (Principle II — enforced by literally calling the same `services/matching.py::match_scholarship` function, not a reimplementation); no live external submission is introduced (Principle III — untouched by this feature); only `source_registry`-approved sources are ever fetched as authoritative, and a candidate's automated validation never self-promotes it (Principle IV — `services/candidate_validation.py` has no write path to `source_registry.status`); `source_monitor` is a workflow, never a sixth agent (FR-MON-5, Agent Architecture constraint).
**Scale/Scope**: Same registry scale as 001 (a handful of curated sources); monitoring runs process the full active-source set per cycle, not a sharded/distributed scan — consistent with the single-instance deployment research.md Decision 1 assumes.

## Constitution Check

*GATE: evaluated before Phase 0 research.*

| Principle | Check | Result |
|---|---|---|
| I. No Fabrication — Grounded Claims Only | `source_monitor` writes scholarship changes through the existing `run_ingestion` workflow unchanged — every field still carries `value_status`/`confidence`/provenance exactly as 001 enforces; coverage/health views report measured counts only (`CoverageSummary.claims_complete_coverage: Literal[False]` already exists and is reused, not reimplemented); candidate-validation results are explicitly advisory, never asserted as fact | PASS |
| II. Deterministic Hard-Constraint Matching | Monitoring-triggered re-matching calls `services/matching.py::match_scholarship` — the identical code path `POST /scholarships/{id}/match` already uses — so there is no second, divergent matching implementation for an LLM to leak into; `source_monitor` itself contains no LLM call at all | PASS |
| III. Human Approval Before Any Live External Submission | Untouched by this feature; no submission code path exists or is added | PASS |
| IV. Registry-Approved Sources Only | Monitoring fetches exclusively through `official_fetch.py`'s existing `source_id`-gated connector (unchanged); candidate-source automated checks (`services/candidate_validation.py`) never write to `source_registry` or flip `candidate_sources.status` — only the existing, unchanged `approve_candidate_source`/`reject_candidate_source` do; a failed fetch is logged as `fail`, never presented as "no scholarships found" (new `used_homepage_fallback`/`fetched_url` columns make this more auditable, not less honest) | PASS |
| V. Spec-Driven Development, Tests + Evals Gated in CI | This plan follows `spec.md`; `tasks.md` (not created here) will follow this plan; CI gains a new dependency-scan step (`pip-audit`) alongside the existing test + eval-score-gate steps, strengthening Principle V's CI-gating rather than weakening it | PASS |
| Agent Architecture constraint (exactly 5 agents) | `source_monitor` is implemented as a `backend/app/workflows/` LangGraph module, structurally identical in kind to `ingestion`/`url_match`/`source_validate` (all existing workflows, not agents) — zero new agents introduced | PASS |
| Technology stack constraint | Every *production* code dependency this plan proposes beyond 001 (APScheduler, `prometheus-client`) is an in-process library addition, not a core-stack replacement (database/vector-store/orchestration-framework unchanged); `pip-audit` is dev/CI-only; Caddy is an infra sidecar, not a backend code dependency. None of these replace FastAPI/Postgres/Qdrant/LangGraph+LangChain, so none triggers the ADR requirement reserved for "replacing or materially changing a core stack component" | PASS |

**No violations.** Complexity Tracking is empty. The four new tools (APScheduler, Caddy, `prometheus-client`, `pip-audit`) are additions alongside the existing stack, not replacements of any constitution-named core component, and each is justified against a specific requirement the current stack cannot meet (`research.md` Decisions 1 and 7) — per the user's own instruction, this is reported explicitly rather than treated as self-evidently fine.

📋 **Architectural-decision check, run per the three-part test (impact / alternatives / cross-cutting scope):** none of this plan's choices clears all three bars for an ADR — the scheduler, metrics library, and TLS sidecar are each a bounded, reversible, single-purpose addition with one clearly smallest-viable option chosen (not a cross-cutting redesign), and none replaces a constitution-named core stack component. No ADR is suggested for this plan. If `/sp.tasks` or implementation later surfaces a reason the in-process scheduler can't hold (e.g. a forced move to multi-instance scaling), that would meet the bar and should be raised then.

## Project Structure

### Documentation (this feature)

```text
specs/002-source-monitoring-hardening/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md         # Phase 1 output
├── contracts/            # Phase 1 output
│   └── openapi.yaml
└── tasks.md              # Phase 2 output — NOT created by this command
```

### Source Code (repository root)

Additive only — every path below is either wholly new or an existing file gaining new, backward-compatible functions (never a rewrite of an existing function's contract). Nothing under `specs/001-scholarship-mvp/` is touched.

```text
backend/
  app/
    api/
      monitoring.py          # NEW — GET/POST /monitoring/runs, GET /monitoring/runs/{id}
      health.py               # NEW — GET /sources/health, GET /sources/{id}/fetch-log
      coverage.py              # NEW — GET /coverage (thin wrapper; aggregation logic lives in services/coverage.py)
      alerts.py                 # NEW — GET /alerts, GET/PUT /alerts/preferences
      sources.py                 # EXTENDED (existing file) — adds GET /sources/candidates/{id}/validations only; no existing route changed
    workflows/
      source_monitor/
        __init__.py               # NEW
        graph.py                   # NEW — the deterministic monitoring workflow (research.md Decision 2)
    services/
      lifecycle.py                  # NEW — FR-LIFECYCLE-2..5 transition logic (extends, does not replace, services/verification.py's existing compute_lifecycle_status)
      alerts.py                      # NEW — NotificationChannel Protocol + InAppChannel/EmailChannel + alert-generation rules (FR-ALERT-1..6)
      candidate_validation.py         # NEW — FR-CANDVAL-1 automated checks, composed from existing app/tools/*
      retention.py                     # NEW — NFR-HARDEN-7 fetch-log pruning job
      coverage.py                       # EXTENDED (existing file) — add candidate-pending count + verified-opportunity count to compute_coverage_summary; existing function signature/behavior for 001's callers unchanged
    sources/
      connectors/
        official_fetch.py               # EXTENDED (existing file) — fetch_and_extract_listing prefers listing_page_url when set, records fetched_url/used_homepage_fallback; existing call signature unchanged for 001 callers
    scheduling/
      scheduler.py                       # NEW — APScheduler setup/teardown, wired into main.py's lifespan
      locks.py                            # NEW — Postgres advisory-lock helper (FR-MON-6)
    core/
      rate_limit.py                       # NEW — in-memory token-bucket Starlette middleware (NFR-HARDEN-2)
      logging.py                           # NEW — structured JSON log formatter (NFR-HARDEN-4)
      metrics.py                            # NEW — prometheus-client registry + /metrics route glue
      config.py                              # EXTENDED (existing file) — adds SMTP_*, MONITORING_INTERVAL_MINUTES, FETCH_LOG_RETENTION_DAYS, HEALTH_WINDOW_N, RATE_LIMIT_PER_MINUTE settings, all optional with defaults
    models/
      source.py                              # EXTENDED (existing file) — adds listing_page_url/freshness_window_days to SourceRegistry, fetched_url/used_homepage_fallback/monitoring_run_id to SourceFetchLog; no existing column/enum changed
      monitoring.py                           # NEW — MonitoringRun ORM model
      alert.py                                 # NEW — Alert, AlertPreferences ORM models
      candidate_validation.py                   # NEW — CandidateSourceValidation ORM model
    schemas/
      monitoring.py                              # NEW
      health.py                                   # NEW
      coverage.py                                  # NEW (request/response shapes only; CoverageSummary itself stays in schemas/discovery.py, extended there)
      alert.py                                      # NEW
      candidate_validation.py                        # NEW
    data/
      repositories/
        source_repo.py                               # EXTENDED (existing file) — adds read helpers (consecutive-failure count, fetch-log-with-url queries); no existing function's behavior changed
        monitoring_repo.py                              # NEW
        alert_repo.py                                    # NEW
        candidate_validation_repo.py                      # NEW
    main.py                                               # EXTENDED (existing file) — registers new routers, lifespan-starts/stops the scheduler, mounts rate-limit middleware and /metrics
  tests/
    workflows/test_source_monitor.py                       # NEW
    services/test_lifecycle.py, test_alerts.py,
             test_candidate_validation.py, test_retention.py  # NEW
    core/test_rate_limit.py                                     # NEW
    api/test_monitoring.py, test_health.py,
        test_coverage.py, test_alerts_api.py                     # NEW
  migrations/
    versions/
      ...                                                          # NEW — two migrations per data-model.md's Cross-cutting notes
docker-compose.podman.yml                                             # EXTENDED — adds the caddy sidecar service
Caddyfile                                                               # NEW — TLS/reverse-proxy config
.env.example                                                             # EXTENDED — new optional vars from quickstart.md
```

**Structure Decision**: Reuse the existing `backend/app/` modular-monolith skeleton exactly as 001 left it. New capabilities populate new files inside the already-existing `workflows/`, `services/`, `api/`, `models/`, `schemas/`, `data/repositories/` packages (plus two genuinely new packages this feature needs: `app/scheduling/` for the in-process trigger, and nothing else at the top level). No existing file's existing function signature changes; three existing files (`services/coverage.py`, `sources/connectors/official_fetch.py`, `data/repositories/source_repo.py`, `core/config.py`, `models/source.py`, `api/sources.py`, `main.py`) gain new, additive functionality alongside what 001 already put there.

## Post-Design Constitution Check

*Re-run after Phase 1 design (data-model.md, contracts/, quickstart.md).*

| Principle | Design-time verification | Result |
|---|---|---|
| I. No Fabrication | `data-model.md`'s `alerts.change_summary` and `candidate_source_validations` are explicitly provenance-carrying/advisory-only by design (never a bare assertion); `CoverageSnapshot.claims_complete_coverage` in `contracts/openapi.yaml` is a literal-`false` enum in the schema itself, not just a convention | PASS |
| II. Deterministic Hard-Constraint Matching | `data-model.md`'s `alerts.match_id` FK points at the existing immutable `matches` table — monitoring never writes a competing verdict shape; `services/lifecycle.py` (per data-model.md §3) is specified as pure/deterministic with no LLM call | PASS |
| III. Human Approval Before Submission | No submission-related path appears anywhere in `contracts/openapi.yaml` or `data-model.md`; unaffected | PASS |
| IV. Registry-Approved Sources | `contracts/openapi.yaml`'s `/sources/candidates/{id}/validations` is read-only (GET only) — no endpoint exists anywhere in this contract that could promote a candidate outside 001's existing approve/reject routes; `data-model.md` §7 explicitly states the validations table is never joined into an authoritative path | PASS |
| V. Spec-Driven, Tests+Evals in CI | `quickstart.md` maps every one of spec.md's 8 user stories to a concrete, runnable verification sequence against the contracts and data model produced here | PASS |

No violations surfaced during design. Complexity Tracking remains empty.

## Complexity Tracking

*No entries — Constitution Check passed with no violations at either gate.*
