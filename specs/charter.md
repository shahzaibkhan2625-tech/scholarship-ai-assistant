# Charter — Scholarship AI Assistant

**Tier:** T0 (context-ladder) — always-loaded, 1-page summary. Deeper detail lives in ADRs and per-workstream specs, not here.

## Vision

Help students find, understand, and apply to scholarships they're actually eligible for, without ever guessing on their behalf.

## Agents (exactly 5)

1. **Main Orchestrator** — routes user intent to the right sub-agent(s) and owns the overall conversation/session state.
2. **Research/Q&A** — answers questions about scholarships, eligibility, and process using only retrieved, sourced content.
3. **Discovery** — searches and surfaces candidate scholarships from approved sources.
4. **Application/Submission** — assists drafting and assembling application materials; never submits anything live without a human clicking "go."
5. **Matching (guardrailed)** — evaluates hard eligibility constraints (deadlines, GPA, citizenship, degree level, etc.) deterministically; this is a rules/guardrail layer, not a free-form LLM judgment.

## Tech Stack

- **Backend:** FastAPI (Python 3.12)
- **Database:** PostgreSQL via Neon
- **Vector store:** Qdrant Cloud
- **Agent orchestration:** LangGraph + LangChain, OpenAI Agents SDK
- **Infra:** Docker, GitHub Actions CI
- **Frontend (later phase):** React + TypeScript

## Hard Rules

These are non-negotiable and apply across all agents and phases:

1. **Never fabricate facts.** If a claim isn't grounded in retrieved, sourced content, the agent says it doesn't know — it does not guess.
2. **Hard-constraint matching results are never overridden by an LLM.** The Matching agent's deterministic eligibility checks (deadlines, GPA cutoffs, citizenship, degree level, etc.) are final; no LLM layer may relax or reinterpret a hard constraint.
3. **No live external submission without explicit human approval.** The Application/Submission agent may draft, assemble, and stage materials, but nothing is sent to a third-party system until a human explicitly approves that specific submission.
4. **Only registry-approved sources are treated as authoritative.** Research/Q&A and Discovery may only cite/rely on sources that are in the approved source registry; unvetted web content is not treated as ground truth.

## Status

Phase 0 (scaffolding). No auth, database models/migrations, frontend, or real agent/matching/Q&A logic exists yet — those land in later workstreams.
