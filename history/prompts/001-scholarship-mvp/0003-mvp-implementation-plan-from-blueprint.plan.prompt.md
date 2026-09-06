---
id: 0003
title: MVP implementation plan from blueprint
stage: plan
date: 2026-09-06
surface: agent
model: claude-sonnet-5
feature: 001-scholarship-mvp
branch: 001-scholarship-mvp
user: shahzaib2625khan@gmail.com
command: /sp.plan
labels: [plan, architecture, mvp, phase-1, matching-guardrails, source-registry]
links:
  spec: specs/001-scholarship-mvp/spec.md
  ticket: null
  adr: null
  pr: null
files:
 - specs/001-scholarship-mvp/plan.md
 - specs/001-scholarship-mvp/research.md
 - specs/001-scholarship-mvp/data-model.md
 - specs/001-scholarship-mvp/contracts/openapi.yaml
 - specs/001-scholarship-mvp/quickstart.md
tests:
 - none run or added (planning artifact only; eval/test cases are Phase-1 implementation work, referenced in quickstart.md)
---

## Prompt

Use specs/scholarship-ai-assistant-master-blueprint-v1.md (v1.6) as the authoritative architecture source for this plan — read it and the constitution first. Technology stack: Python 3.12, FastAPI as a modular monolith (app/ split into api, orchestration, agents, workflows, services, sources, tools, schemas, rag, data, models, core per the blueprint), Pydantic for schemas, SQLAlchemy + Alembic over PostgreSQL (Neon managed), Qdrant Cloud for vector storage, LangGraph + LangChain and the OpenAI Agents SDK for agent orchestration, MCP for tool access, Docker + GitHub Actions for infra/CI, React + TypeScript for the frontend (later phase). Architecture: exactly five agents (Main Orchestrator, Research/Q&A, Discovery, Application/Submission, Matching-guardrailed); document processing, CV/SOP generation, application planning, and profile management are deterministic workflows/services with LLM tools for fuzzy parts — NOT agents — per the blueprint's Architecture Decision Matrix. Enforce the constitution's non-negotiables in the design: hard-constraint matching runs in a deterministic hard_constraints service outside the LLM and is never overridden; the Matching agent has schema-locked output, evidence-required, no-guess, bounded tools (no web/fetch), and deterministic ranking in code; only registry-approved sources are authoritative; no live external submission (MVP stages materials for human-approved sending only); every scholarship field carries value-status + provenance; RAG grounds Q&A with Verified/Inferred/Unknown labels. Reuse the already-built Phase 0 foundation (auth, users table, DB layer, Docker, CI, eval-harness skeleton) rather than redesigning it. Derive the data model from the spec's 9 key entities and define API contracts per capability. Respect the blueprint's phase ordering (Phase 1 = profile + URL-match + Q&A first).

## Response snapshot

Ran `/sp.plan` for feature 001-scholarship-mvp. Read spec.md, constitution.md (v1.0.0), the full master blueprint v1.6 (all 44 sections), charter.md, and the existing Phase 0 backend code (`backend/app/` — auth, users table, DB layer, config, Docker, CI, eval-harness skeleton) to confirm what already exists and must be reused unchanged.

Produced:
- **plan.md** — Technical Context (Python 3.12 backend-only; frontend explicitly deferred), a Constitution Check gate (both pre- and post-Phase-1-design) verifying all 5 constitution principles + the agent-architecture/tech-stack constraints pass with zero violations and no ADR trigger (this plan operationalizes already-ratified architecture, introduces no new decision), a phase-ordering table mapping spec user stories to blueprint phases (Phase 1: US1/US2/US3; Phase 2: US4; Phase 3: US5; Phase 4: US6), and a full `backend/app/` project-structure tree marking every existing Phase-0 file as reused-as-is and every new module with the phase that introduces it.
- **research.md** — 7 rationale/alternatives entries (D1–D7) covering agent-orchestration choice, Postgres-vs-Qdrant storage boundary, guardrailed Matching agent, source governance, RAG grounding-check escalation, workflow-vs-agent classification for doc/CV/SOP/planning/profile, and eval-harness reuse — plus an explicit list of blueprint items already marked ⚠️ NOT YET DEFINED that are confirmed non-blocking for this plan (hosting provider, MCP server provider, registry seed list, eval thresholds, etc.).
- **data-model.md** — all 9 spec Key Entities expanded into concrete PostgreSQL tables (profiles + education_records/test_scores/experience/profile_criteria/missing_info; scholarships + universities/programs/scholarship_fields/requirements/funding_details/scholarship_sources; source_registry/candidate_sources/source_fetch_log; matches; Q&A as a stateless schema, no table; application_documents; generated_documents; applications/tasks; submission_approvals), each with validation rules and state transitions tied back to specific FRs and constitution principles.
- **contracts/openapi.yaml** — full OpenAPI 3.0 contract for every capability in §5.1 (profile CRUD + criteria + CV-import, url-match, Q&A, match-my-profile, discovery, documents, CV/SOP generation, application planner/tracker/assistant/submission-approval), tagged by implementing phase, with schema-locked `MatchVerdict`, value-status/confidence vocabulary, and a `submission_approvals` endpoint scoped per-submission (no live-send endpoint exists anywhere in the contract, per FR-APP-4).
- **quickstart.md** — run instructions plus curl-based manual verification walkthroughs for every US1/US2/US3 acceptance scenario, a pointer to where Phase-1 eval cases will live, and a Phase-1 non-negotiables checklist.

Ran `.specify/scripts/powershell/update-agent-context.ps1 -AgentType claude`; it reported success but produced no actual content change to CLAUDE.md (confirmed via hash comparison against HEAD) since this project's CLAUDE.md predates and doesn't carry the template's insertion markers — non-blocking, since the tech stack is already fully specified in the constitution CLAUDE.md defers to.

## Outcome

- ✅ Impact: Full Phase-0-output artifact set for `/sp.tasks` to consume — plan.md, research.md, data-model.md, contracts/openapi.yaml, quickstart.md all created under specs/001-scholarship-mvp/. Constitution Check passed at both gates with zero violations; no ADR suggested (no new architectural decision was introduced beyond what the constitution/blueprint already ratify).
- 🧪 Tests: None run/added — this is a planning-only artifact; real Phase-1 test/eval cases are implementation work for `/sp.tasks` → `/sp.implement`.
- 📁 Files: specs/001-scholarship-mvp/{plan.md, research.md, data-model.md, contracts/openapi.yaml, quickstart.md} created; CLAUDE.md inspected, unchanged.
- 🔁 Next prompts: `/sp.tasks` to decompose Phase 1 (profile + url-match + qa) into testable tasks; optionally `/sp.adr` if a future phase's implementation surfaces a genuine architectural choice not already covered here.
- 🧠 Reflection: The blueprint and constitution had already made every architecturally significant decision for this feature (5 agents, guardrailed Matching, registry governance, storage boundaries) — this plan's job was faithfully deriving a concrete data model and API surface from them, not deciding anything new, which is why the Constitution Check passed cleanly with no Complexity Tracking entries.

## Evaluation notes (flywheel)

- Failure modes observed: none — no gate failures, no unresolved NEEDS CLARIFICATION markers (all Technical Context fields were resolvable directly from the constitution/blueprint).
- Graders run and results (PASS/FAIL): N/A — no eval graders apply to a planning artifact.
- Prompt variant (if applicable): N/A
- Next experiment (smallest change to try): When `/sp.tasks` runs for Phase 1, verify the matching-correctness eval fixture matrix (research.md D7 / quickstart.md) can be authored directly from data-model.md's `profile_criteria`/`matches` schemas without further clarification.
