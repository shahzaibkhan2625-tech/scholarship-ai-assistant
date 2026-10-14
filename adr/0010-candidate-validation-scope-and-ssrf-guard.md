# ADR-0010: Candidate-Source Validation — Scope Split, SSRF Guard, and the LLM-Cost Correction

- **Status:** Accepted
- **Date:** 2026-10-13
- **Feature:** 002-source-monitoring-hardening (US7 — candidate validation, FR-CANDVAL-1..4)
- **Context:** US7 adds advisory reachability / extractability / official-signal checks for `pending` candidate sources. Phase A review found three things the spec, research and tasks did not settle: (1) the monitor-run trigger (T244) sits behind a US8 task, (2) the checks fetch URLs taken from arbitrary third-party pages, and (3) research Decision 6 described the service as "deterministic, no LLM", which is not true.

## Decision

1. **Split US7 at the `graph.py` boundary; defer T244 and the workflow half of T240.** T244 depends on T236 (US8) and the agreed `graph.py` edit order is "U1 → … → U8 → U7" (tasks.md, Dependencies). US8 has not started. Built now: T238, T239, T241, T242, T243, T245 (service, repo, schema, read-only API, their tests), none of which touch `source_monitor/graph.py`. Deferred: T244 and the "validated during a run / exception never fails the run" tests of T240, to be wired in alongside US8's `graph.py` work (carry-forward note in tasks.md). Until then candidates are validated only when `validate_candidate` is called; no scheduler path calls it, so **FR-CANDVAL-1's "automatically" is not yet satisfied end to end**. The trigger accepts lag: a candidate may wait for the next scheduled run (up to `MONITORING_INTERVAL_MINUTES`); no inline validation at record time, which would put a fetch and an LLM call inside the discovery fetch path.

2. **SSRF guard in the validation service (not in the original task list).** Candidate URLs originate from links on third-party listing pages, and the low-level `fetch_url` follows redirects with no host restrictions. The older `source_validate` workflow did the same, but only for a URL a human chose to validate; an automatic run turns attacker-influenced link text into server-side requests, so loopback, private, link-local (cloud metadata, `169.254.169.254`) and reserved targets must be refused. `unsafe_url_reason` allows only `http`/`https` and refuses any host that is, or resolves to, a non-global address (including IPv4-mapped IPv6, `localhost`). It is applied to the requested URL (no request is sent if refused) and to `final_url` after redirects (content is discarded and the row records `blocked: redirect target …`). A host that does not resolve is left to the fetch to report as unreachable. DNS and fetch seams are injected so the guard is testable without a network.

3. **Correct research Decision 6: the service is not LLM-free.** Extractability calls `extract_listing_from_page`, one LLM call per validated candidate; only its grounding check is deterministic. The official-signal heuristics are pure and deterministic but are written fresh in the service (the claimed TLD patterns do not exist in `detect_candidate_links.py`). The per-run cap (`MAX_CANDIDATE_VALIDATIONS_PER_RUN=15`) and recheck window (`CANDIDATE_VALIDATION_RECHECK_DAYS=7`) exist because of that cost.

4. **Supporting choices.** Rows are append-only with `checked_at` set Python-side (an injectable clock), because the column's `server_default now()` is the transaction start time and rows written in one transaction would tie. The service ends its read transaction before any network or LLM call and writes with a short insert afterwards, so a pooled Neon backend is never held across the slowest external call; there is no advisory lock (overlapping runs can at worst append a duplicate advisory row). Signals are advisory JSON with no score or verdict; the only API addition is `GET /sources/candidates/{id}/validations`.

## Consequences

### Positive

- The independent 80% of US7 ships and is testable now; the dependent wiring lands once, with US8's `graph.py` changes, rather than being edited twice.
- A closed, tested boundary on what an automatically fetched candidate URL can reach; an AST test also keeps promotion and status writes out of the module (Principle IV, SC-006).

### Negative / known gaps (named, not hidden)

- **The SSRF guard is not airtight.** (a) `fetch_url` follows redirects internally, so a redirect to a non-public address has already been *requested* when `final_url` is checked; the guarantee is that its content is discarded and never mined, not that the request was never sent. (b) DNS is resolved by the guard and again by `httpx`, so a rebinding host could differ between the two. Closing both needs a redirect-aware fetch (no automatic follow, per-hop check, pinned IP), a change to the shared `web_fetch` tool that is out of scope here.
- Until T244 lands there is no automatic trigger, so reviewers see "no validation yet" for every candidate.
- Each validated candidate costs an LLM call; a candidate whose page is not a listing page will read `extractable=false`, which is accurate but not necessarily disqualifying.

## Amendment (US8 B1, recorded in B4)

T244 (the monitor-run trigger) and the workflow half of T240 landed in commit `857422a`, after US8's `graph.py` work, exactly as decision 1 planned. The "no automatic trigger / reviewers see 'no validation yet'" consequence above therefore no longer applies; the SSRF limits and every other decision here are unchanged and are carried into `docs/operations-hardening.md` section 11.

## Alternatives Considered

- **Do US8's `graph.py` work first so US7 lands whole.** Rejected: blocks seven independent tasks on an unstarted story.
- **Wire T244 now against the pre-US8 `graph.py`.** Rejected: guarantees a second edit and merge of the same file, against the agreed order.
- **Skip the SSRF guard and rely on the human gate.** Rejected: the fetch happens before any human sees the candidate.
- **Modify `web_fetch.fetch_url` to expose redirects.** Deferred: a shared-tool change affecting 001 callers; the limits above are documented instead.
- **Inline validation when the candidate is recorded.** Rejected: adds a fetch and an LLM call to the discovery fetch path (Q2).

## References

- Spec: `specs/002-source-monitoring-hardening/spec.md` (US7, FR-CANDVAL-1..4, SC-006)
- Tasks: T238–T245 (T244 and part of T240 deferred); research.md Decision 6 (corrected)
- Related ADRs: ADR-0001 (registry governance), ADR-0005 (candidate discovery), ADR-0008 (transaction-scoped locks, pooled Neon)
