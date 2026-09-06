---
id: 0004
title: Generate full MVP task breakdown
stage: tasks
date: 2026-09-06
surface: agent
model: claude-sonnet-5
feature: 001-scholarship-mvp
branch: 001-scholarship-mvp
user: shahzaib2625khan@gmail.com
command: /sp.tasks
labels: ["tasks", "mvp", "planning", "traceability"]
links:
  spec: specs/001-scholarship-mvp/spec.md
  ticket: null
  adr: null
  pr: null
files:
 - specs/001-scholarship-mvp/tasks.md
tests:
 - none run (task-generation only; no code executed)
---

## Prompt

/sp.tasks Generate the complete task breakdown for the entire MVP (all phases) from plan.md and its phase-ordering table — Phase 1 (US1 profile, US2 URL-match, US3 Q&A), Phase 2 (US4 discovery), Phase 3 (US5 documents/CV/SOP), Phase 4 (US6 planning + human-approved assistant). Also include a Phase 0 (Foundation) section listing the already-completed work as tasks marked done [X] — repo skeleton, uv/Python 3.12 setup, FastAPI /health, Docker + docker-compose (Neon), GitHub Actions CI + eval-harness skeleton, charter.md, SQLAlchemy+Alembic DB layer with users table on Neon, and JWT auth (signup/login/logout) — so tasks.md is a complete record of the whole project for traceability/demonstration. For all incomplete phases: dependency-ordered (models→services→endpoints), mark [P] parallel-safe tasks, include test tasks and the hard-constraint verdict-correctness test matrix per TDD, and give each task an exact file path. Follow data-model.md, contracts/openapi.yaml, and the constitution's non-negotiables. Reuse the Phase 0 foundation, do not recreate it.

## Response snapshot

Read plan.md, data-model.md, contracts/openapi.yaml, constitution.md, spec.md, the tasks-template, and the existing backend/ tree to ground every task in a real file path. Generated `specs/001-scholarship-mvp/tasks.md` (134 tasks, T001–T134):

- **Phase 0 (Foundation, COMPLETED)**: T001–T011, all marked `[X]`, mapped to actual existing files (pyproject.toml, main.py, Docker/CI, charter.md, db.py/alembic, users table migration, auth.py/security.py/deps.py).
- **Phase 1 (US1+US2+US3, P1)**: T012–T063 — shared RAG/grounding/Qdrant foundational subsection, then per-story TDD blocks (tests → models → migration → repository → service → tools → workflow/agent → endpoint → wiring), including the constitutionally-mandated hard-constraint verdict-correctness test matrix (T034, build-breaking) and Matching-agent guardrail-violation tests (T035), ending with Main Orchestrator routing integration.
- **Phase 2 (US4, P2)**: T064–T092 — Source Registry models/services/connectors/tools, ingestion + source_validate workflows, Discovery agent, source-registry-compliance and conflict-resolution tests.
- **Phase 3 (US5, P3)**: T093–T112 — documents + generated_documents models, doc_pipeline/cv_gen/sop_gen workflows, anti-hallucination eval, ground_check enforcement test.
- **Phase 4 (US6, P4)**: T113–T129 — tasks/submission_approvals models, app_plan/submit_prep workflows, Application/Submission agent, submission-approval-scoping and no-live-send tests.
- **Final Phase**: T130–T134 — quickstart validation, Principle-II/III code-review pass, user_id-isolation audit, OpenAPI drift check, full eval-suite/CI gate run.

Documented four explicit sequencing resolutions at the top of tasks.md (not silently applied): (1) universities/programs/scholarship_fields moved into Phase 1's scholarship.py since scholarships' FKs need them, while scholarship_sources stays Phase 2 (needs source_registry); (2) Q&A has no persisted table per data-model.md §5; (3) a minimal `applications` table + `POST /applications` moved from Phase 4 into Phase 3, because the OpenAPI contract ties document upload to a real `application_id` — Phase 4 then only adds `tasks`/`submission_approvals` onto the same table; (4) `workflows/source_monitor/` is confirmed out of MVP scope, no task generated. Every task cites an exact file path; `[P]` markers reflect genuine file-level independence; dependencies, a parallel-execution example, and a phase-ordered implementation strategy sections were added mirroring the tasks-template.

## Outcome

- ✅ Impact: `specs/001-scholarship-mvp/tasks.md` created — a complete, dependency-ordered, file-path-precise task breakdown spanning the whole MVP (Phase 0 done + Phases 1–4 planned), ready for `/sp.implement` or manual execution.
- 🧪 Tests: No tests executed (planning artifact only); the plan itself embeds mandatory test tasks per story, including the hard-constraint verdict-correctness matrix and guardrail-violation tests required by constitution Principle II.
- 📁 Files: specs/001-scholarship-mvp/tasks.md (created)
- 🔁 Next prompts: `/sp.implement` to begin Phase 1 Foundational + US1; or `/sp.adr` if the user wants the Phase-3/Phase-4 `applications`-table-split resolution formally recorded.
- 🧠 Reflection: The biggest risk to task quality was three separate contract-level inconsistencies across plan.md/data-model.md/openapi.yaml (table ordering vs. FK dependencies, and the /applications/{id}/documents path needing an application_id before POST /applications was scheduled). Resolved all three with documented, low-risk scheduling decisions rather than escalating or silently redesigning.

## Evaluation notes (flywheel)

- Failure modes observed: None during generation; the main hazard avoided was silently "fixing" a cross-document architectural inconsistency without a trace — addressed via the explicit "Resolution notes" section in tasks.md.
- Graders run and results (PASS/FAIL): N/A (no eval harness applies to a tasks.md artifact itself).
- Prompt variant (if applicable): N/A
- Next experiment (smallest change to try): When `/sp.implement` runs, confirm the Phase-3 minimal-`applications`-table resolution against the user's intent before generating code, since it's a scheduling deviation from the literal per-phase table list in data-model.md's cross-cutting notes.
