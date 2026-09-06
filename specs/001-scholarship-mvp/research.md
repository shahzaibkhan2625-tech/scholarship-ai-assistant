# Phase 0 Research: Scholarship AI Assistant — MVP Core Platform

All technology and architecture choices for this feature are already decided by `.specify/memory/constitution.md` and `specs/scholarship-ai-assistant-master-blueprint-v1.md` (v1.6); this document records the rationale and alternatives considered so the decisions are auditable, and separately calls out the handful of items the blueprint itself still marks open (⚠️ NOT YET DEFINED) and explains why none of them block Phase 1 implementation.

## D1. Agent orchestration: OpenAI Agents SDK + LangGraph + LangChain

- **Decision**: Use the OpenAI Agents SDK for the 5 agents (loop, handoffs, tool calling, interactive approval steps, and — critically — **output guardrails** on the Matching agent); use LangGraph for the 9 deterministic, stateful, checkpointed, HITL-capable workflows (`url_match`, `ingestion`, `source_validate`, `doc_pipeline`, `cv_gen`, `sop_gen`, `app_plan`, `submit_prep`, `source_monitor*`); use LangChain for tool/RAG glue (loaders, structured-output calls, retrievers).
- **Rationale**: matches blueprint §21 exactly; the OpenAI Agents SDK's output-guardrail primitive is what makes the Matching agent's schema-lock/evidence-required/no-guess/hard-constraint-read-only guarantees enforceable in code rather than by convention; LangGraph's checkpointing gives workflows resumability across sessions (needed for the Application/Submission agent's per-application persisted progress, §7.4).
- **Alternatives considered**: hand-rolled agent loops (rejected — more error-prone, no built-in guardrail/handoff primitives); ad-hoc orchestration for workflows (rejected — no resumability, harder to reason about HITL pauses).

## D2. Storage: PostgreSQL (Neon) as source of truth, Qdrant Cloud for retrieval only

- **Decision**: All structured/business/provenance/governance data (users, profiles, scholarships, requirements, funding, matches, source registry, applications) lives in PostgreSQL via Neon, managed from Phase 0 onward (not a local container). Qdrant Cloud holds only semantic-retrieval chunks (scholarship page/PDF text, program descriptions, user-document chunks) and is **never** the source of truth for fresh, time-sensitive facts (deadlines, open/closed status, funding) — those are read from Postgres and re-verified live against the official source when stale.
- **Rationale**: blueprint §12–§13 rule, restated in constitution's technology-stack constraint; keeps eligibility-relevant facts auditable and queryable relationally, while still getting semantic search for grounded Q&A.
- **Alternatives considered**: pgvector instead of Qdrant (rejected — weaker metadata-filtering ergonomics at the scale the blueprint targets, §21); using Qdrant payloads as the fact store (rejected — would violate the freshness/verification rule and Principle I).

## D3. Matching: guardrailed agent, not a pure rule-based service

- **Decision**: A deterministic `hard_constraints` service computes hard-constraint/exclusion results before the Matching agent runs and passes them in as read-only context; the Matching agent (OpenAI Agents SDK, output-guardrailed) reasons only over soft preferences and fuzzy/unstructured criteria; a `ranking` service (pure code) produces the final deterministic order.
- **Rationale**: constitution Principle II (non-negotiable) and blueprint §7.5/§18; soft/fuzzy criteria (e.g. "relevant work experience") genuinely benefit from LLM judgment, while hard eligibility facts must be as auditable as a unit test.
- **Alternatives considered**: pure rule-based matching (rejected — can't reason over fuzzy criteria per FR-MATCH-6); unguardrailed LLM matching (rejected — violates Principle II, risks a hard-constraint override).

## D4. Source governance: human-approved registry, no LLM self-authorization

- **Decision**: `source_registry` (active/pending/disabled) is the only authority connectors may treat as ground truth; a newly observed source is written to `candidate_sources` as `pending` and promoted to `active` only via the `source_validate` workflow's human-approval gate.
- **Rationale**: constitution Principle IV; blueprint §29; FR-DISC-3/5, FR-VERIFY-2.
- **Alternatives considered**: LLM-adjudicated source trust (rejected — explicitly prohibited by the constitution); fully automatic candidate promotion after reachability checks (rejected — blueprint A10 requires the approval to stay human even when checks are automated).

## D5. Q&A and grounding: RAG with a grounding-check escalation loop (Self-RAG pattern)

- **Decision**: Research/Q&A agent retrieves from Qdrant first; a `ground_check` node evaluates whether retrieved context supports the answer; if weak, the agent escalates to a live `official_fetch`/`web_fetch` MCP call rather than answering from unsupported context. Every answer carries Verified/Inferred/Unknown + source + last-verified date, matching the fixed §34 response schema.
- **Rationale**: constitution Principle I; blueprint §7.2/§12; this is the concrete mechanism behind FR-QA-2/3 and SC-008.
- **Alternatives considered**: RAG-only with no escalation (rejected — would produce stale or absent answers for facts not yet indexed); free-text LLM answers (rejected — violates FR-OUT-1's structured-response requirement).

## D6. Deterministic workflows/services vs. agents for document/CV/SOP/planning/profile

- **Decision**: Document intake (`doc_pipeline`), CV/SOP generation (`cv_gen`/`sop_gen`), application planning (`app_plan`), and profile management (`profile` service) are LangGraph workflows / plain services with narrowly-scoped LLM tools for their fuzzy sub-steps (`extract_requirements`, `extract_profile`, `ground_check`) — never independent agents.
- **Rationale**: constitution's Agent Architecture constraint (non-negotiable without an ADR); blueprint §9 Architecture Decision Matrix — each of these capabilities follows a predictable, fixed step sequence with no open-ended tool-selection decision, which is exactly the workflow/service classification criterion.
- **Alternatives considered**: promoting any of these to an agent (rejected — would require a written justification per the constitution and an ADR before implementation; no such justification exists or is warranted, since the branching in each case is data-dependent within a fixed graph, not open-ended planning).

## D7. Testing & evals: reuse Phase 0's harness, don't rebuild it

- **Decision**: `backend/evals/runner.py` and `.github/workflows/ci.yml`'s eval-gate wiring (already present from Phase 0) are extended with real cases starting in Phase 1 (matching-verdict correctness + guardrail-violation build-breakers, Q&A groundedness, extraction/verification quality), rather than replaced.
- **Rationale**: constitution Principle V requires the eval harness + CI score-gate to exist from the earliest phase, not be deferred — Phase 0 already satisfies this; blueprint §25.1.
- **Alternatives considered**: none — rebuilding an already-compliant harness would violate the "smallest viable change" default policy.

## Items the blueprint itself leaves open (non-blocking for this plan)

These are explicitly marked ⚠️ NOT YET DEFINED in the blueprint and are **operational/config decisions**, not architecture — none of them block Phase 1 data-model or contract design, and none require a NEEDS CLARIFICATION marker in Technical Context above:

- Backend-hosting provider + object storage for the end-of-Phase-2 minimal cloud runtime (§21, §22) — only needed when Phase 2 ships.
- Frontend component library, shadcn/ui vs. MUI (§27 A4) — frontend is out of scope for this plan.
- Exact Source Registry seed list and numeric coverage/freshness/KPI targets (spec Assumptions, PRD OD-4/OD-5) — operational config, set when Phase 2's registry is seeded.
- Eval score thresholds / regression tolerance / checker-accuracy bar (§25.1) — set when the first real eval cases are authored in Phase 1 tasks, not at plan time.
- MCP server providers for `web_fetch`/`search` (§27 A5) — chosen when Phase 1's `url_match` workflow is implemented; the tool *interface* (`tools/web_fetch.py`, typed I/O) is fixed now regardless of provider.
- Isolated sandbox execution mechanism for tool actions reaching live external systems (§9.1) — relevant starting Phase 2 (external fetches) and critical by Phase 6 (live submission); no MVP code path in this plan reaches a live external system without it already being a `web_fetch`/`official_fetch` call inside the existing tool boundary.

None of these affect the Phase 1 data model or API contracts below.
