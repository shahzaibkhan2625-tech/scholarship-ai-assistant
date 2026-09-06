<!--
Sync Impact Report
==================
Version change: (none — template) → 1.0.0
Rationale: Initial ratification (first concrete version) → 1.0.0.

Sources consulted (in addition to the template):
  - specs/charter.md (T0 Hard Rules, 5-agent map, tech boundaries)
  - specs/scholarship-ai-assistant-master-blueprint-v1.md (§7.5 guardrails a–f,
    §25/§25.1 tests vs. evals + CI score-gate, §29 Source Registry governance,
    §32 verification/conflict resolution, §33 failure/retry, §37 submission safety)
  - specs/scholarship-ai-assistant-prd-v1.md (FR-MATCH-5/6, FR-APP-3/OD-2,
    FR-VERIFY-1..7, FR-OUT-1..3, Part B agent behavior contracts)

Modified principles:
  - [PRINCIPLE_1_NAME] → "I. No Fabrication — Grounded Claims Only (NON-NEGOTIABLE)"
  - [PRINCIPLE_2_NAME] → "II. Deterministic Hard-Constraint Matching (NON-NEGOTIABLE)"
  - [PRINCIPLE_3_NAME] → "III. Human Approval Before Any Live External Submission (NON-NEGOTIABLE)"
  - [PRINCIPLE_4_NAME] → "IV. Registry-Approved Sources Only (Authoritative Sourcing)"
  - [PRINCIPLE_5_NAME] → "V. Spec-Driven Development — Specs and Evals Gated in CI (NON-NEGOTIABLE)"

Added sections:
  - "Agent Architecture & Technology Constraints" (replaces [SECTION_2_NAME])
  - "Development Workflow & Quality Gates" (replaces [SECTION_3_NAME])
  - Governance (procedure, versioning policy, compliance review)

Removed sections:
  - [PRINCIPLE_6_NAME] placeholder slot — dropped intentionally. The governing
    sources define exactly 5 non-negotiable principles; no 6th exists at this time.

Templates requiring updates:
  - ✅ .specify/templates/plan-template.md — Constitution Check gate is generic/dynamic
       ("[Gates determined based on constitution file]"); no stale references to fix.
  - ✅ .specify/templates/spec-template.md — generic, no hardcoded principle references.
  - ✅ .specify/templates/tasks-template.md — generic, no hardcoded principle references.
  - ✅ .claude/commands/sp.constitution.md — generic, no stale agent-specific references.
  - ⚠ specs/charter.md — pending manual sync check: this constitution is now authoritative;
       charter.md's Hard Rules section should be reviewed for wording drift on next edit.

Follow-up TODOs:
  - None. RATIFICATION_DATE and LAST_AMENDED_DATE both set to today because no prior
    constitution version existed before this run.
-->

# Scholarship AI Assistant Constitution

## Core Principles

### I. No Fabrication — Grounded Claims Only (NON-NEGOTIABLE)

Every factual claim, scholarship detail, eligibility statement, or answer surfaced to a user
MUST be grounded in retrieved, sourced content. If an agent cannot ground a claim, it MUST say
so explicitly ("Unknown" / "could-not-confirm") rather than inferring, extrapolating, or
guessing. Hedged language ("likely", "probably") presented as if it were fact is prohibited — an
ungrounded claim must be labeled Unknown, not softened. Every field that can be missing MUST
carry an explicit value-status (`known / unknown / not_applicable / conditional / conflicting`)
plus a confidence label (`verified / inferred / unknown`); missing information is stored as
`unknown`, never invented. Generated materials (CV, SOP, other written content) MUST be grounded
strictly in the user's real profile and documents — never fabricated achievements, experience,
publications, employment, grades, or certificates.

**Rationale**: Scholarship eligibility, deadlines, and requirements carry real financial and
academic consequences for students. A plausible-sounding hallucination is strictly worse than an
honest "I don't know," because it is actionable and indistinguishable from a verified fact.

### II. Deterministic Hard-Constraint Matching (NON-NEGOTIABLE)

Hard eligibility constraints (GPA thresholds, degree level, nationality, deadlines, mandatory
tests, and other binary/rule-based criteria) MUST be computed by a deterministic pre-step (e.g. a
`hard_constraints` service) and passed into the Matching agent as **read-only** context. The
Matching agent's LLM reasoning is scoped to soft preferences and fuzzy/unstructured criteria only
(e.g. "relevant work experience," "research fit") and MUST NOT alter, override, or re-derive a
hard-constraint result; any output that contradicts one MUST be rejected, not merely flagged.
The Matching agent's output MUST additionally satisfy:
- **Schema-locked output** — no free-text verdicts; output validates against a fixed verdict schema.
- **Evidence-required** — every matched/failed/unmet criterion carries an evidence reference, or the output is rejected by an output guardrail.
- **No-guess rule** — any field with `value_status = unknown` surfaces as `missing_information`, never invented.
- **Bounded tool access** — no web/search/fetch tools are reachable from this agent.
- **Deterministic ranking** — the ranking formula lives in code; the agent supplies inputs but never reorders results itself.

A fixed verdict-correctness test matrix (known profile × known scholarship → expected verdict)
MUST run on every build, and any hard-constraint guardrail violation MUST be a build-breaking CI
failure, not a warning.

**Rationale**: Hard constraints are factual/legal gates (e.g. a citizenship requirement or a
passed deadline). Any non-determinism here produces false eligibility that wastes a student's
effort or causes a rejected application; these checks must be as auditable as a unit test.

### III. Human Approval Before Any Live External Submission (NON-NEGOTIABLE)

The Application/Submission agent MAY draft, assemble, and stage application materials, but MUST
NOT transmit anything to a third-party system, portal, or address until a human has explicitly
approved that specific submission. Approval MUST be scoped to the exact submission being sent —
an earlier general "go ahead" does not carry forward to a different or later submission. When
live submission ships: retries MUST be limited to safe/idempotent operations; the system MUST NOT
blindly repeat a dangerous or irreversible action; submission status MUST be communicated clearly
to the user; failures MUST be recorded and made visible; and a final user review opportunity MUST
occur immediately before the irreversible submit action, in addition to the earlier approval gate.

**Rationale**: Submitting on a student's behalf without a final human checkpoint is an
irreversible, high-consequence action (wrong scholarship, stale materials, duplicate
submission). A human-in-the-loop gate — reinforced by a last-moment review — is the only
acceptable safeguard.

### IV. Registry-Approved Sources Only (Authoritative Sourcing)

Research/Q&A and Discovery agents MUST only cite, retrieve from, or treat as ground truth sources
present in the approved Source Registry; the LLM never self-authorizes a domain as authoritative.
A newly observed source MUST enter the registry as a `pending` candidate and MUST NOT be treated
as authoritative until a human approves it via the governed candidate-source workflow; connectors
MUST reject any fetch whose source is not `active` in the registry. A failed or timed-out source
fetch MUST be logged and retried per policy — it MUST NOT be interpreted or reported as "no
scholarship found." When sources conflict, resolution follows a deterministic policy (official
over third-party, more-recent over older, higher-reliability over lower); an unresolved conflict
MUST be surfaced as `conflicting` with both sources shown, never silently resolved by LLM choice.
Coverage MUST be reported as measured (sources checked/failed/gaps), never asserted as complete.

**Rationale**: Scholarship information changes frequently and is easy to spoof or let go stale.
Authority must be an explicit, auditable, human-governed allowlist — not an implicit trust of
"whatever the web search happened to return," and a fetch failure must never quietly masquerade
as a fact ("this scholarship doesn't exist").

### V. Spec-Driven Development — Specs and Evals Are First-Class, Gated in CI (NON-NEGOTIABLE)

No implementation work begins without a preceding spec (`specs/<feature>/spec.md`) and, where
architecturally significant, a plan (`plan.md`) and tasks (`tasks.md`). Tests and evals are
distinct and both mandatory:
- **Tests** are pass/fail checks on fixed cases (unit, API, agent, tool, RAG, matching, document,
  anti-hallucination, source-governance, connector-contract, ingestion, failure/retry, coverage,
  response-schema, security).
- **Evals** are a scored grading layer over agent outputs (matching-verdict correctness, Q&A
  groundedness/evidence, extraction & verification quality). The checker/grader itself MUST be
  validated against a trusted labeled set before its scores are trusted.

CI MUST run a score-regression gate: a change that lowers eval scores MUST be blocked from
merging, and any hard-constraint guardrail violation (Principle II) remains an absolute,
build-breaking failure regardless of aggregate score. The eval harness and CI score-gate MUST
exist from the project's earliest phase — established alongside the first agent capability, not
deferred. A change to agent behavior lacking a corresponding spec update, passing tests, or a
passing eval is out of compliance and MUST NOT merge.

**Rationale**: This project's core risk is ungrounded or non-deterministic agentic behavior at
scale across five semi-autonomous agents. Specs-first development plus CI-gated tests *and*
scored evals is the only practical mechanism to keep that behavior verifiably aligned with the
Hard Rules as the system grows.

## Agent Architecture & Technology Constraints

- **Exactly five agents**, as defined in `specs/charter.md`: Main Orchestrator, Research/Q&A,
  Discovery, Application/Submission, and Matching (guardrailed). Introducing a sixth agent, or
  materially redefining an existing agent's responsibility boundary, is an architecturally
  significant decision and MUST be captured in an ADR before implementation. Document processing,
  CV/SOP generation, application planning, and profile management are deliberately deterministic
  workflows/services with LLM tools for the fuzzy parts — not agents — per the project's
  Architecture Decision Matrix; a proposal to turn a workflow/service into an agent MUST first
  justify in writing why the existing agent set cannot absorb the responsibility.
- Agent responsibility boundaries in `specs/charter.md` are authoritative. Any spec or plan that
  blurs those boundaries (e.g., Research/Q&A performing submission actions, or Discovery
  bypassing the source registry) MUST be rejected at the Constitution Check gate.
- **Harness discipline**: every agent operates inside bounded permission walls (e.g., the
  Matching agent has no web/search/fetch tools) plus automatic non-LLM checks that verify its
  output (grounding checks, schema/evidence/no-guess guardrails, verification/conflict-resolution
  services). Any tool action that reaches a live external system — and especially future
  browser/portal interaction for assisted submission — MUST execute inside an isolated, safe
  environment so a faulty action cannot affect the host, user data, or the external system without
  passing the harness.
- **Technology stack constraints**: FastAPI on Python 3.12 (backend), PostgreSQL via Neon
  (database), Qdrant Cloud (vector store), LangGraph + LangChain and the OpenAI Agents SDK
  (agent orchestration), Docker + GitHub Actions (infra/CI), React + TypeScript (frontend, later
  phase). Qdrant is never the source of truth for fresh, time-sensitive facts (deadlines,
  open/closed status, funding) — those are read from PostgreSQL structured records and re-verified
  live against the official source when stale. Replacing or materially changing a core stack
  component (database, vector store, orchestration framework) is architecturally significant and
  requires an ADR.

## Development Workflow & Quality Gates

- Every feature follows: spec → (plan, when architecturally significant) → tasks →
  implementation (red/green/refactor). Specs precede code, without exception (Principle V).
- Every user prompt produces a Prompt History Record (PHR) under `history/prompts/`, routed per
  the rules in `CLAUDE.md` (constitution / `<feature-name>` / general). PHRs are the audit trail
  for how agent behavior evolved and MUST NOT be skipped.
- Architecturally significant decisions — long-term impact, multiple viable alternatives
  considered, or cross-cutting scope — trigger an ADR suggestion. ADRs are never auto-created;
  they always require explicit user consent before being written.
- CI MUST run automated tests plus the eval suite described in Principle V, covering at minimum:
  (a) no-fabrication/grounding checks, (b) hard-constraint matching determinism (golden-file or
  property tests against the Matching agent's rules code, and the guardrail-violation build-break
  check), and (c) source-registry compliance for Research/Q&A and Discovery (candidate-not-
  auto-trusted, disabled-source-blocks-fetch, failed-fetch-is-not-"none-found"). A pull request
  that reduces coverage in any of these three areas MUST NOT merge.
- Code review MUST explicitly verify that no change lets LLM logic reach into the deterministic
  Matching path (Principle II violation) and that no new code path calls a live external
  submission API without the human-approval gate (Principle III violation).
- Fresh work sessions are artifact-driven, not conversation-driven: a session's required context
  is the project charter, the relevant phase/feature spec, and repository contracts (schemas,
  OpenAPI, ADRs) — never a replay of prior chat history. Continuity between sessions is carried in
  specs, contracts, PHRs, and ADRs.

## Governance

This constitution supersedes conflicting guidance in specs, plans, tasks, ADRs, or command
files. Where a conflict is found, this constitution governs until formally amended.

**Amendment procedure**: Amendments are proposed by running `/sp.constitution` with the change
description. The command drafts updated principles/sections, computes the semantic version bump,
produces a Sync Impact Report, and requires explicit user confirmation before the file is
overwritten. No agent may edit this file outside that flow.

**Versioning policy**: Semantic versioning, `MAJOR.MINOR.PATCH`:
- **MAJOR** — backward-incompatible principle removal or redefinition.
- **MINOR** — a new principle or section is added, or existing guidance is materially expanded.
- **PATCH** — wording, typo, or other non-semantic clarifications.

**Compliance review**: Every `/sp.plan` run MUST execute the Constitution Check gate, once before
Phase 0 research and again after Phase 1 design. Any violation MUST be either resolved or
explicitly justified in the plan's Complexity Tracking table. Every code review and CI run
implicitly re-validates Principles I–V through the tests and evals described in "Development
Workflow & Quality Gates."

`specs/charter.md` remains the always-loaded, one-page summary of these rules; `specs/
scholarship-ai-assistant-master-blueprint-v1.md` and `specs/scholarship-ai-assistant-prd-v1.md`
are the detailed architecture and product sources this constitution was derived from. If any of
these ever diverge from this constitution, this constitution is authoritative and the other
document MUST be updated to match.

**Version**: 1.0.0 | **Ratified**: 2026-09-06 | **Last Amended**: 2026-09-06
