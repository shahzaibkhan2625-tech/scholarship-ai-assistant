# ADR-0005: New-Source Discovery via Deterministic Link Extraction, Not Active Search

> **Scope**: Document decision clusters, not individual technology choices. Group related decisions that work together (e.g., "Frontend Stack" not separate ADRs for framework, styling, deployment).

- **Status:** Accepted
- **Date:** 2026-10-01
- **Feature:** 001-scholarship-mvp (US4 Acceptance Scenario 3 — candidate source discovery)
- **Context:** spec.md US4 Scenario 3 / FR-DISC-5 requires that a website encountered during discovery but not yet in the governed Source Registry be recorded as a `pending` candidate, never treated as authoritative. T135 (listing-page extraction) resolved Scenario 1 but explicitly left Scenario 3 open (`app/agents/discovery/agent.py` module docstring: "nothing here observes or proposes sources outside the already-active registry"). The `candidate_sources` table, its repository functions, and the `source_validate` human-approval workflow (T086) already exist and are fully tested — `create_candidate_source` had zero production callers before this decision. The spec itself is silent on *how* an unrecognized site is supposed to be "found," leaving the trigger mechanism to be designed.

## Decision

1. **Trigger: deterministic `<a href>` link extraction from already-fetched listing-page HTML, not active/unrestricted web search.** `fetch_and_extract_listing` (the T135 connector, `app/sources/connectors/official_fetch.py`) already fetches raw HTML from an active, governance-gated source. This decision adds a link-extraction pass over that same raw HTML (before it is tag-stripped for the listing-extraction LLM call) rather than introducing any new fetch/search capability. FR-DISC-2 prohibits unrestricted web search as a discovery strategy, and the existing `search_tool` (`app/tools/search.py`) is unwired from discovery entirely and, by design, discards non-registry domains rather than surfacing them — the opposite of what Scenario 3 needs. Reusing already-gated content keeps the new surface area to zero new network access.

2. **Detection is a deterministic filter, never an LLM judgment call.** A link is flagged only if: its domain differs from the source's own domain; its domain is not already known to `source_registry` in *any* status (`get_source_by_domain`); its domain is not on a fixed denylist of social/infra/consent-vendor domains; and its link text or path contains one of a fixed keyword set (`scholarship`, `fellowship`, `bursary`, `stipend`, `studentship`, `grant`, `funding`). No model is asked "is this relevant" — constitution Principle IV forbids the LLM self-authorizing what counts as a source, and this extends to forbidding it from even nominating candidates by soft judgment.

3. **Connector-tier placement, forced by an existing guardrail, not chosen freely.** `tests/agents/test_discovery_guardrails.py` already includes `create_candidate_source` in `_FORBIDDEN_SOURCE_REPO_WRITE_METHODS` for `app/agents/discovery/agent.py` — the Discovery Agent module is AST-statically barred from ever calling it. The new write path therefore lives in `official_fetch.py`, exactly where `log_fetch`/`mark_source_failing` already live for T135's own accounting — the Discovery Agent continues to see only already-shaped tool output and never touches `source_repo` writes.

4. **Precision over recall, with a hard per-fetch cap.** The filter is intentionally conservative: it will under-flag real new sources that don't happen to match the keyword set, and an honest estimate is that 30-60% of what it does flag will still be reviewer-rejected noise. This is accepted because rejecting a pending candidate is cheap (an existing, tested one-call action) and flooding the reviewer queue is the worse failure mode. To bound the worst case explicitly, a single `fetch_and_extract_listing` call may flag **at most 5** unique-domain candidates (document-order truncation, links beyond the cap dropped silently — this is a best-effort discovery bound, not a completeness guarantee, and is therefore not logged as a coverage gap). Cross-run dedup (domain already in `source_registry` → no-op; domain already a pending candidate → bump a `seen_count`/`last_seen_at` signal instead of inserting a duplicate row) keeps repeated fetches of the same page from compounding noise over time.

## Consequences

### Positive

- Scenario 3 becomes exercisable end-to-end without adding any new fetch tool, network egress point, or LLM-judged relevance step — the smallest-surface-area option available.
- The existing guardrail test suite continues to hold as the enforcement mechanism (extended, not weakened): the agent module still cannot self-authorize a source, and a new connector-tier guardrail test closes the one gap this decision opens (a write-capable module that didn't previously need AST scanning).
- Fully deterministic and unit-testable in isolation (Slice 1) before any DB/network wiring is added, mirroring the T135 slicing discipline.

### Negative

- Recall is intentionally poor: a new source whose page doesn't use one of the fixed keywords in anchor text/path will simply never be flagged by this mechanism. Scenario 3 is satisfied ("when found, recorded correctly"), but this decision does not attempt to maximize how often a new source *is* found.
- The denylist/keyword lists are hand-maintained constants; they will need periodic upkeep as real-world page structures are observed (tracked the same way `docs/SOURCES.md` tracks seed-source facts).
- The 5-per-fetch cap means a single page with many legitimately-new scholarship-provider links could lose some beyond the 5th to silent truncation in one run — acceptable per this decision, but worth surfacing if it's ever observed in practice via live-smoke testing (Slice 4).
- `record_or_bump_candidate_source`'s dedup check (`list_candidate_sources(status=None)` + in-Python domain comparison) is **O(n) in the total `candidate_sources` row count**, evaluated on every qualifying link. This is acceptable at MVP scale — candidates are rare, human-reviewed events, not a high-volume table — but is an explicit scale assumption, not an oversight: if `candidate_sources` ever grows into the thousands, this should be revisited (e.g., add an indexed `domain` column instead of deriving it from `url` per row at read time).

## Alternatives Considered

- *Unrestricted web search as the trigger* — rejected: directly conflicts with FR-DISC-2 and would require standing up a new, currently-unconfigured search capability (`search_tool`'s `raw_search` has no backend wired in) purely for this feature.
- *LLM-judged relevance ("does this link look like a scholarship source?")* — rejected: reintroduces exactly the self-authorization risk Principle IV exists to prevent; a deterministic filter is auditable the same way the constitution requires hard-constraint checks to be.
- *Flag every non-registry domain unconditionally* — rejected: the noise estimate (A6) makes this an unacceptable reviewer-time cost with no cap; a link farm or footer sitemap could produce dozens of junk rows from a single fetch.
- *No per-fetch cap* — rejected after review: unbounded worst-case queue growth from one adversarial/junk-heavy page is an avoidable risk for a five-line guard.
- *Store dedup metadata via a schema migration (new `domain`/`seen_count` columns)* — rejected: the existing `signals` JSONB column already supports this with zero schema change and matches the existing in-place-mutation pattern `reject_candidate_source` already uses.

## References

- Feature Spec: `specs/001-scholarship-mvp/spec.md` (US4 Acceptance Scenario 3, FR-DISC-5)
- Implementation Plan: `specs/001-scholarship-mvp/plan.md`
- Related ADRs: ADR-0001 (source registry governance — table-separation enforcement this decision extends)
- Related tests: `backend/tests/agents/test_discovery_guardrails.py`, `backend/tests/sources/test_source_registry_compliance.py`, `backend/tests/workflows/test_source_validate.py`
