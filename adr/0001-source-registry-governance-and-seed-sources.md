# ADR-0001: Source Registry Governance and Seed Sources

> **Scope**: Document decision clusters, not individual technology choices. Group related decisions that work together (e.g., "Frontend Stack" not separate ADRs for framework, styling, deployment).

- **Status:** Accepted
- **Date:** 2026-09-08
- **Feature:** 001-scholarship-mvp (WS2.1 — Source Registry + governance)
- **Context:** Phase 2 (Blueprint §22) requires scholarship discovery to run only against **approved** sources, never freely-decided-by-the-LLM domains (Blueprint §29). Before any connector or Discovery Agent work (WS2.2+) could begin, three things needed deciding: which sources seed the registry, how governance (candidate → approved) is actually enforced, and how the `api_connector` interface gets proven correct without a live paid API to test against.

## Decision

1. **Five-source seed set, selected for source-type + region diversity, not volume.** `backend/app/data/seeds/seed_sources.yaml` seeds exactly: DAAD (`national_education`, Germany), the EACEA Erasmus Mundus catalogue (`international_org`, EU-wide), Stipendium Hungaricum (`gov`, Hungary), HEC Pakistan (`gov`, Pakistan), and KAUST Admissions (`university`, Saudi Arabia). Criteria: (a) each is the *first-party* administrator of the scholarships it lists — no aggregators; (b) together they cover 4 of the 12 `source_type` values and 3 regions, satisfying the Phase-2 DoD's "≥2 distinct source types" with headroom; (c) each was reachable and inspectable at seeding time. Full per-source justification is in `docs/SOURCES.md`, which is the living record to update as sources are added/approved/disabled.

2. **Governance is enforced by table separation + a status filter, not by prompt instruction.** `source_registry` (authoritative) and `candidate_sources` (unvetted, `pending/approved/rejected`) are distinct tables with no shared identity space. `source_repo.get_active_sources` / `get_source_by_id` query `source_registry` only and additionally filter `status == active` (excluding `pending`, `disabled`, and `failing`). A `candidate_sources` row can never be "joined as authoritative" because it structurally lives in a table nothing in the read path queries — promotion requires `approve_candidate_source`, which inserts a *new* `source_registry` row from a reviewer-confirmed payload; nothing auto-derives a full registry entry from a candidate's thin `url`/`signals` data, since that would mean inventing facts (name, reliability, extraction rules) the system hasn't verified (constitution Principle I). This is enforced at the repository/DB layer, so it holds regardless of what any LLM agent (Discovery Agent, WS2.2+) is told to do.

3. **No free production scholarship API exists at this budget/reliability bar, so `api_connector`'s contract is proven against a local fixture.** No freely-available, sufficiently reliable scholarship-listing API was identified for the MVP. Building `api_connector` (WS2.2) against a live paid API now would mean taking on a vendor dependency before one is evaluated/budgeted, and would make its tests non-deterministic/costly. Its contract (`source_id` in → typed `{content, status, fetched_at, source_url}` out, Blueprint §15) is instead proven with a local fixture server/mock standing in for a real API response shape. Swapping in a real vendor later is a fixture-vs-live config change, not a contract change.

## Consequences

### Positive

- Discovery can start from day one of WS2.2 with real, verifiable sources instead of a synthetic fixture-only registry.
- The "never auto-trust a new source" guarantee is testable by construction (a governance-bypass bug shows up as a failing repository/route test, not a prompt-adherence audit) — see `backend/tests/sources/test_source_registry_compliance.py`.
- `api_connector`'s interface can be built, tested, and reviewed now without blocking on a vendor/budget decision.

### Negative

- Five sources is thin coverage — most countries/scholarship types have no seeded source yet; broader coverage depends on the `candidate_sources` discovery + approval pipeline (WS2.2+), which is out of scope for this workstream.
- `robots.txt`/ToS compliance for all five seeded domains is currently **UNVERIFIED** (tracked in `docs/SOURCES.md`) — connectors must not fetch until that review lands, which gates WS2.2's first real fetch, not just its code.
- The fixture-tested `api_connector` gives no guarantee about a real vendor's actual response shape/rate limits until one is chosen; that risk is deferred, not eliminated.

## Alternatives Considered

**Seed set:**
- *Seed a scholarship aggregator instead of/alongside first-party sources* — rejected: an aggregator's own sourcing and freshness aren't verifiable transitively, which would undermine the "verified, source-attributed" product promise (spec.md SC-002/SC-003) from the very first seeded row.
- *Seed zero sources, rely entirely on candidate discovery* — rejected: leaves Phase 2's discovery flow untestable end-to-end until the (separate, later) Discovery Agent workstream produces its first candidate, blocking WS2.2 unnecessarily.

**Governance enforcement:**
- *Single `sources` table with a `is_approved` boolean* — rejected: a boolean flag is one missed `WHERE` clause away from a candidate leaking into an authoritative read path; a separate table makes that class of bug structurally harder to introduce.
- *Rely on agent/prompt instructions ("only use approved sources")* — rejected outright per constitution Principle IV: governance must not depend on an LLM reliably following an instruction.

**`api_connector` proof:**
- *Delay `api_connector` until a paid API is chosen* — rejected: blocks WS2.2's connector-interface work on a vendor/procurement decision with no fixed timeline.
- *Mock at the HTTP-library level inside tests only, no fixture server* — considered acceptable alternative; a fixture file/server was preferred so the same fixture can double as a manual integration-testing target outside pytest.

## References

- Feature Spec: `specs/001-scholarship-mvp/spec.md`
- Implementation Plan: `specs/001-scholarship-mvp/plan.md`, `specs/001-scholarship-mvp/data-model.md` §3
- Blueprint: `specs/scholarship-ai-assistant-master-blueprint-v1.md` §14, §29, §33
- Related docs: `docs/SOURCES.md`
- Related ADRs: none yet (first ADR in this repo)
