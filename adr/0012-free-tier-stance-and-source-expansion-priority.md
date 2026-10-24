# ADR-0012: Stay on the Free Tier for Now, and Pull Source Expansion Forward to Build After 003 and 004

> **Scope**: Document decision clusters, not individual technology choices. Group related decisions that work together (e.g., "Frontend Stack" not separate ADRs for framework, styling, deployment).

- **Status:** Accepted
- **Date:** 2026-10-25
- **Feature:** Roadmap decision spanning 003-006 (source: `docs/blueprint-gap-analysis.md` §5-§7)
- **Context:** The gap analysis found that background monitoring alone needs roughly 48 Gemini extraction calls a day (estimate) against an observed free-tier cap of 20 a day, and that source coverage is far below the product goal of national, regional/state and university sources in any country. Blueprint §36 labels "large-scale source/country expansion" as Future/Advanced (Phase 6) and Blueprint A11 seeds only a small registry. Pulling expansion forward changes that label, and it multiplies LLM cost unless quota is governed first, so the owner's order of work and the paid-tier question both need recording.

## Decision

1. **Stay on the Gemini free tier for now.** No paid key is purchased or assumed by 003-005. The paid-tier question is decided at the start of feature 006, when the cost of monitoring and validating many more sources is known from 003's budget data.
2. **Source expansion (feature 006) is pulled forward in priority.** It supersedes the Blueprint §36 "Phase 6" label for source/country expansion. It is built **after 003 (quota governance) and 004 (trustworthy records)**, because expanding coverage before quota is governed and records are trustworthy would multiply both LLM spend and untrusted data. Build order is therefore 003, 004, 006, 005, 007, then 008/009 (008 can start in parallel once the API surface is stable). Folder numbers are stable identifiers, not build order.
3. **Expansion stays inside the existing governance rules.** FR-DISC-2 (no unrestricted web search as the primary strategy), FR-DISC-5 (new sources are candidates only) and Blueprint A10 (human approval) are unchanged. Expansion means curated official directories, bulk candidate import with deterministic pre-checks, and a reviewer queue, not an LLM deciding what is authoritative.
4. **Source approval must be restricted to operators.** Today any logged-in user can approve or reject a source (`api/sources.py`; `User` has no role). A minimal operator restriction ships in feature 004 as its own small slice; richer roles and a reviewer queue land in 006.
5. **006's spec must carry a per-source LLM cost budget** and the paid-tier decision as its first open question.

## Consequences

### Positive

- Quota is governed before any feature adds load, so new work does not compete unpredictably with monitoring.
- Coverage work starts earlier than the Blueprint planned, matching the owner's stated priority.
- The security gap in source approval is closed early, in a small slice, instead of waiting for a large feature.

### Negative

- Matching any profile (005) is built after source expansion, so for a period more sources exist than the matcher can evaluate well.
- Staying on the free tier caps what 003-005 can do: multi-user batch matching and heavy generation do not fit 20 calls a day (estimate), and the roadmap defers that trade-off to 006.
- This reverses a Blueprint phase label; the Blueprint text itself is not edited, so a reader must consult this ADR to see the current order.

## Alternatives Considered

- **Move to a paid key now.** Rejected for now: the owner prefers to learn the real cost profile from 003's budget guard first.
- **Keep expansion in Phase 6 as the Blueprint says.** Rejected: it conflicts with the stated product goal of broad, multi-level source coverage.
- **Build expansion before 003/004.** Rejected: it multiplies LLM calls and unverified records before either is controlled.

## References

- `docs/blueprint-gap-analysis.md` (M-08, M-09, M-10, M-36; §5 quota model; §6 roadmap; §7 decisions)
- Blueprint §29, §30, §36, A10, A11; PRD FR-DISC-2, FR-DISC-5
- Related ADRs: ADR-0001 (source registry governance), ADR-0005 (new-source discovery), ADR-0010 (candidate validation), ADR-0013 (verification semantics)
