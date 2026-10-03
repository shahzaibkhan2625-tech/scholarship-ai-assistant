# Feature Specification: Source Monitoring, Coverage Analytics & Production Hardening

**Feature Branch**: `002-source-monitoring-hardening`
**Created**: 2026-10-03
**Status**: Draft
**Input**: User description: "Source monitoring, coverage analytics, and production hardening for Scholarship AI Assistant — Phase 5 of the Master Blueprint. Scheduled source monitoring that re-checks approved registry sources, detects new/changed/closed/reopened/stale scholarships, and flows changes through the existing ingestion pipeline (deterministic workflow, not a new agent). Re-match affected scholarships against owner profiles using the existing guardrailed matching capability and alert owners (in-app + email, extensible to other channels). Source-health visibility so a failed fetch is never shown as 'no scholarships found'. Coverage analytics over the registry and fetch history. Record lifecycle states (newly discovered, verified, unverified, updated, expired, closed, reopened, stale, source-unavailable), with stale records flagged. Monitoring and discovery must fetch each source's configured listing page, not the domain homepage. Semi-automated candidate-source validation (reachability, extractability, official-source signals) with mandatory human approval before a source becomes authoritative. Production hardening: HTTPS, rate limits, retry/backoff, observability, backups, persistent document storage across redeploys, and a documented fetch-history retention policy."

**Source of truth**: `specs/scholarship-ai-assistant-prd-v1.md` (FR-DISC-4, FR-VERIFY-5, FR-COV-1, NFR-REL-1, NFR-REL-2, NFR-REL-3/OD-8, KPI-1, KPI-2), `specs/scholarship-ai-assistant-master-blueprint-v1.md` (§22 Phase 5, §30.4 Coverage Engine, §33 Failure & Retry, §35 Continuous Monitoring), and `.specify/memory/constitution.md` (non-negotiable behavioral guarantees). This spec translates the blueprint's Phase 5 scope into testable user stories, functional requirements, and success criteria for the production-hardening and continuous-monitoring capability layered on top of `specs/001-scholarship-mvp/`. It intentionally excludes architecture/tech-stack decisions, which belong to a future `plan.md`, and does not modify `specs/001-scholarship-mvp/` in any way.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Scheduled monitoring catches a changed scholarship (Priority: P1)

An approved source in the registry is re-checked automatically on a recurring schedule, without any user initiating a search. The monitoring run re-fetches the source's configured listing page, compares the result against stored scholarship records, and detects a newly published scholarship, a changed requirement/deadline/funding amount, a scholarship that closed or reopened, or a record that has gone stale. Whatever is detected flows through the same ingestion pipeline used by on-demand discovery, so the detected change is normalized, verified, and stored the same way a manually discovered scholarship would be.

**Why this priority**: This is the core value proposition of Phase 5 (blueprint §35, PRD NFR-REL-3): the product catches changes proactively instead of only when a student happens to search again. Every other story in this feature (re-match/alert, health visibility, coverage, lifecycle states) depends on monitoring runs actually happening and actually detecting change correctly.

**Independent Test**: Can be fully tested by seeding one approved registry source with a known scholarship record, altering the source's underlying content (new scholarship added, a deadline changed, a scholarship marked closed), triggering a scheduled monitoring run, and verifying the stored scholarship record reflects the change with correct provenance — independent of alerting, coverage analytics, or candidate-source validation.

**Acceptance Scenarios**:

1. **Given** an approved source with one previously stored scholarship, **When** a scheduled monitoring run re-fetches that source and finds a new scholarship listed, **Then** the new scholarship is ingested through the standard ingestion pipeline and recorded with `lifecycle_status = newly_discovered`.
2. **Given** a stored scholarship whose deadline or funding amount changed on the official source, **When** a monitoring run re-fetches that source, **Then** the stored record is updated, the prior values remain auditable, and the record's `lifecycle_status` reflects `updated`.
3. **Given** a stored scholarship that is no longer listed as open on its official source, **When** a monitoring run detects this, **Then** the record's `lifecycle_status` transitions to `closed` (or `reopened` if it was previously closed and is listed again).
4. **Given** a stored scholarship whose source has not been successfully re-checked within its configured freshness window, **When** the freshness window elapses without a successful re-check, **Then** the record is flagged `stale` rather than silently continuing to display as current.
5. **Given** a source with no changes since the last check, **When** a monitoring run completes, **Then** no spurious change records are created and the source's `last_checked_at` is updated.

---

### User Story 2 - Re-match and alert the affected owner (Priority: P1)

When monitoring detects a new or changed scholarship, every profile owner for whom that scholarship is or might be relevant is automatically re-matched against the updated scholarship record using the existing guardrailed matching capability (same deterministic hard-constraint evaluation as on-demand matching). Owners whose match outcome is newly favorable, or whose previously matched scholarship changed in a way that affects their eligibility or interest, receive an alert.

**Why this priority**: Detection without notification delivers no value to the student — this is the other half of the end-to-end "catch it and tell me" promise (blueprint §35, PRD OD-8). It is P1 alongside monitoring itself because a monitoring pipeline that never surfaces results to an owner is not yet a complete capability.

**Independent Test**: Can be fully tested by seeding a profile that would newly become eligible for a scholarship once a detected change occurs (e.g., a deadline extension), triggering the change through monitoring, and verifying the owner receives an in-app and email alert whose match verdict matches what on-demand matching would produce for the same inputs — independent of how the change was originally detected.

**Acceptance Scenarios**:

1. **Given** a monitoring-detected new scholarship and an owner profile whose hard constraints it satisfies, **When** re-matching runs, **Then** the same deterministic hard-constraint evaluation used by on-demand matching produces the verdict (no LLM override of hard-constraint results), and the owner is alerted.
2. **Given** a monitoring-detected change to a scholarship the owner was previously matched against, **When** the change affects a hard constraint (e.g., deadline now passed), **Then** the owner's existing match verdict is updated accordingly and the owner is alerted to the change.
3. **Given** an owner with alert preferences enabled for both in-app and email, **When** an alert-worthy event occurs, **Then** the owner receives both an in-app notification and an email containing the scholarship, what changed, and a link/reference to view details.
4. **Given** a monitoring-detected change that does not affect any owner's eligibility or saved/matched scholarships, **When** re-matching runs, **Then** no alert is sent for that change.
5. **Given** the alerting mechanism, **When** a new notification channel is added in the future, **Then** the existing alert-generation logic does not need to be redesigned to support it (channel delivery remains extensible, per OD-8).

---

### User Story 3 - See source health, not silent failure (Priority: P1)

A product operator or student-facing coverage view shows, for every registry source, whether its most recent monitoring/discovery fetch succeeded or failed, and when. A source that failed to fetch is visibly distinguished from a source that was fetched successfully and simply had no new scholarships.

**Why this priority**: This directly enforces constitution Principle IV and blueprint §33 — a failed fetch must never be interpreted as "no scholarships found." Without this visibility, monitoring failures are invisible and coverage claims become silently wrong. It is foundational to trusting everything else in this feature.

**Independent Test**: Can be fully tested by forcing one source's fetch to fail (e.g., simulate unreachable/timeout) and one source's fetch to succeed with zero new scholarships, running monitoring, and verifying the health view distinguishes the two outcomes with correct status, timestamp, and error information — independent of coverage analytics aggregation or alerting.

**Acceptance Scenarios**:

1. **Given** a source fetch that fails (unreachable, timeout, or extraction error), **When** the monitoring run completes, **Then** the outcome is recorded as a failure (not as "zero scholarships found") with a timestamp and failure reason, and the source is retried per the configured retry/backoff policy.
2. **Given** a source fetch that succeeds and finds no new or changed scholarships, **When** the monitoring run completes, **Then** the outcome is recorded as a success with zero changes, visibly distinct from a failure.
3. **Given** a source that has failed its last N consecutive fetches (N configured per policy), **When** the health view is displayed, **Then** that source is flagged as failing/unhealthy rather than simply omitted from view.
4. **Given** a source's listing page structure changes such that extraction fails, **When** this is detected, **Then** the prior good record is preserved (never overwritten with empty results) and the source is flagged for re-check.
5. **Given** the health view, **When** viewed at any time, **Then** every registry source shows its current status (active/failing/disabled), last-checked time, and last-success time.

---

### User Story 4 - Coverage analytics across the registry (Priority: P2)

A coverage view exposes measurable statistics derived from the source registry and fetch history: how many sources are registered, active, successfully checked, and failed; when each was last checked; which countries and providers are covered; how many new candidate sources have been discovered; and where coverage gaps exist.

**Why this priority**: Delivers PRD FR-COV-1 and KPI-1/KPI-2 as an ongoing, Phase-5-grade capability (the MVP only had basic counts). It builds on User Story 3's per-source health data, so it is prioritized after health visibility but before the lower-frequency candidate-validation and hardening work.

**Independent Test**: Can be fully tested by seeding a registry with a known mix of active, failing, and pending-candidate sources spanning multiple countries, then verifying the coverage view's aggregate counts, last-checked times, country/provider coverage, and gap list are arithmetically correct against that seed data — independent of monitoring schedules actually running.

**Acceptance Scenarios**:

1. **Given** a registry with a known number of registered, active, checked, and failed sources, **When** the coverage view is requested, **Then** each count matches the actual registry/fetch-log state.
2. **Given** sources spanning multiple countries and providers, **When** the coverage view is requested, **Then** it lists which countries and providers are covered and identifies gaps (e.g., countries/regions with no active source).
3. **Given** the coverage view, **When** displayed, **Then** it never states or implies complete/100% global coverage — coverage is presented strictly as measured counts and known gaps.
4. **Given** a newly discovered candidate source pending approval, **When** the coverage view is requested, **Then** the candidate is counted separately from approved/active sources.
5. **Given** fetch history accumulated over multiple monitoring runs, **When** the coverage view is requested, **Then** last-checked times reflect the most recent run per source, not a stale aggregate.

---

### User Story 5 - Distinguish a scholarship's lifecycle state at a glance (Priority: P2)

Every scholarship record is tagged with one clear lifecycle state — newly discovered, verified, unverified, updated, expired, closed, reopened, stale, or source-unavailable — so a student (and the product's own logic) can tell at a glance whether a scholarship's details are current, in question, or no longer applicable.

**Why this priority**: PRD FR-VERIFY-5 and NFR-REL-1 require this as first-class, and it is the mechanism by which User Stories 1–4 report their outcomes consistently. It is P2 because the MVP (001) already carries basic verification status; this story extends it to the full lifecycle vocabulary needed for monitoring.

**Independent Test**: Can be fully tested by walking one seeded scholarship record through each lifecycle transition (discovered → verified → updated → stale → source-unavailable → closed → reopened) via simulated monitoring events and confirming the displayed state matches the expected transition at each step — independent of real scheduled runs or alerting.

**Acceptance Scenarios**:

1. **Given** a scholarship record at any point in time, **When** it is displayed anywhere in the product, **Then** it carries exactly one of the nine defined lifecycle states.
2. **Given** a record whose freshness window has elapsed without successful re-verification, **When** the state is evaluated, **Then** it is flagged `stale`.
3. **Given** a record whose source could not be reached across retries, **When** the state is evaluated, **Then** it is flagged `source_unavailable`, not `expired` or `closed` (which require confirmed source content, not absence of a fetch).
4. **Given** a record in the `unverified` state, **When** a subsequent successful monitoring fetch confirms its details against the official source, **Then** it transitions to `verified`.
5. **Given** a record marked `closed`, **When** a later monitoring run finds it listed as open again, **Then** it transitions to `reopened`, not back to `newly_discovered`.

---

### User Story 6 - Monitoring and discovery fetch the right page (Priority: P2)

For any registry source that has a configured listing page (the specific page where scholarships are actually listed, as opposed to the organization's general homepage), both scheduled monitoring and on-demand discovery fetch that configured listing page rather than the domain's homepage.

**Why this priority**: This is a correctness prerequisite for every other story in this feature — if monitoring fetches the wrong page, change-detection is comparing against irrelevant content and every downstream signal (new/changed/closed, health, coverage) becomes unreliable. It is P2 rather than P1 because it is a defect-prevention/accuracy requirement rather than a new user-facing capability, but it blocks correct operation of User Stories 1–5.

**Independent Test**: Can be fully tested by configuring one registry source with a listing page distinct from its homepage, running both discovery and monitoring against it, and confirming the fetched content and resulting records derive from the configured listing page (verifiable via logged fetch URL), not the homepage — independent of any other monitoring behavior.

**Acceptance Scenarios**:

1. **Given** a registry source with a configured listing-page URL different from its homepage, **When** a monitoring run fetches that source, **Then** the logged fetch URL is the configured listing page.
2. **Given** a registry source with a configured listing-page URL, **When** on-demand discovery fetches that source, **Then** it also uses the configured listing page, not the homepage.
3. **Given** a registry source with no configured listing page, **When** it is fetched by monitoring or discovery, **Then** the system falls back to the domain homepage and this fallback is recorded for operator visibility.
4. **Given** a source's configured listing page changes (operator updates the registry entry), **When** the next monitoring run occurs, **Then** it uses the newly configured page.

---

### User Story 7 - Candidate sources get automated pre-checks before human approval (Priority: P3)

When discovery or monitoring surfaces a new candidate source not yet in the approved registry, the candidate automatically runs through a set of checks — is it reachable, can content actually be extracted from it, does it show signals of being an official/authoritative source (e.g., government or institutional domain patterns, stated affiliation) — and the results of those checks are presented to a human reviewer. The candidate remains non-authoritative until a human explicitly approves it, regardless of how well it scores on the automated checks.

**Why this priority**: Reduces reviewer effort (blueprint §22 Phase 5, §29) but is explicitly **semi-automated** — the human approval gate (constitution Principle IV) is non-negotiable and already exists in the MVP. This story only adds assistive automation on top of an existing gate, so it is lower priority than the monitoring/alerting/visibility stories that deliver the feature's core new behavior.

**Independent Test**: Can be fully tested by introducing one new candidate source with known reachability/extractability/official-signal characteristics and confirming the automated check results are recorded and visible to a reviewer, while verifying the candidate cannot be used as an authoritative fact source and does not become `active` without an explicit human approval action.

**Acceptance Scenarios**:

1. **Given** a newly discovered candidate source, **When** automated validation runs, **Then** it records reachability (pass/fail), extractability (pass/fail), and official-source signal findings, visible to a human reviewer.
2. **Given** a candidate source that passes all automated checks, **When** no human has yet approved it, **Then** it remains non-authoritative and is never used as a fact source by discovery, monitoring, Q&A, or matching.
3. **Given** a candidate source that fails one or more automated checks, **When** a human reviewer views it, **Then** the specific failing check(s) are visible, but the reviewer MAY still approve it (automated checks inform, never override, human judgment).
4. **Given** a human reviewer approves a candidate source, **When** approval is recorded, **Then** the source transitions to `active` and becomes usable by discovery and monitoring from that point forward.
5. **Given** a human reviewer rejects a candidate source, **When** rejection is recorded, **Then** the source is not retried as a candidate without a new discovery event surfacing it again.

---

### User Story 8 - The deployed product is production-hardened (Priority: P2)

The deployed instance of the product serves all traffic over HTTPS, enforces request rate limits to protect against abuse, retries/backs off on failed external fetches instead of failing immediately, emits structured logs and metrics an operator can inspect, has backups in place for its data, persists uploaded documents so they survive a redeploy, and follows a documented retention policy for how long fetch-history records are kept.

**Why this priority**: This is explicit Phase 5 scope (blueprint §22 Phase 5) and a precondition for safely running scheduled monitoring unattended in production — without it, a scheduled job failure, a storage wipe on redeploy, or an unbounded abuse pattern could silently corrupt the very data (source health, coverage, uploaded documents) the other stories in this feature depend on. It is P2 because the monitoring/alerting/visibility behaviors (P1) are the feature's primary new value, but hardening must land before this feature is considered safely operable.

**Independent Test**: Can be fully tested by: confirming the deployed endpoint only serves HTTPS (HTTP is redirected or rejected); confirming a burst of requests beyond the configured rate limit is rejected rather than degrading the service; confirming a simulated external-fetch failure is retried per policy before being marked failed; confirming structured logs/metrics are emitted for a monitoring run; confirming an uploaded document is still retrievable after a redeploy; and confirming the fetch-history retention policy is documented and enforced (old records are pruned/archived per policy, not kept forever by accident).

**Acceptance Scenarios**:

1. **Given** the deployed product, **When** accessed over plain HTTP, **Then** the connection is upgraded to HTTPS or rejected — no sensitive traffic is served unencrypted.
2. **Given** a client issuing requests beyond the configured rate limit, **When** the limit is exceeded, **Then** further requests are rejected with a clear rate-limit response rather than degrading or crashing the service.
3. **Given** an external source fetch that fails transiently, **When** the retry/backoff policy applies, **Then** the fetch is retried the configured number of times with increasing backoff before being marked a failure.
4. **Given** a monitoring run or API request, **When** it completes, **Then** structured logs and metrics are recorded sufficient to diagnose what happened without reproducing the request.
5. **Given** a document uploaded by a user, **When** the deployed instance is redeployed, **Then** the document remains retrievable afterward (it was not stored only on ephemeral local disk).
6. **Given** the fetch-history log accumulating entries over time, **When** entries exceed the documented retention period, **Then** they are pruned or archived according to that documented policy, not retained indefinitely by default.
7. **Given** a data-loss scenario (simulated), **When** recovery from backup is attempted, **Then** the product's data can be restored to a recent backup point.
8. **Given** externally-sourced content fetched from a registry source (including candidate sources), **When** it is stored or displayed, **Then** it has been validated and sanitized first; and **Given** the deployed product's configuration and dependency set, **When** inspected, **Then** no secret/credential is found in source control and dependency scans report zero known-critical vulnerabilities.

---

### Edge Cases

- What happens when two monitoring runs for the same source overlap (e.g., a run takes longer than the schedule interval)? → A source is not fetched concurrently by two overlapping runs; the later run either waits or is skipped, never double-processed.
- What happens when a monitoring run detects a change but the re-match/alert step fails after the change was already stored? → The scholarship record update stands (it was correctly detected and stored); the alert failure is itself logged and does not silently disappear, and is retried or surfaced as an operational issue separate from data correctness.
- What happens when an owner has disabled alerts entirely? → Monitoring and re-matching still run and update records, but no alert is sent to that owner; the owner can still see changes by viewing the product directly.
- What happens when a source is disabled/deactivated by an operator while scheduled monitoring is running? → The in-flight run for that source completes or aborts cleanly, and the source is excluded from subsequent scheduled runs until reactivated.
- What happens when a candidate source's reachability/extractability characteristics change between discovery and human review? → The automated checks are re-run (or their recency is shown) so a reviewer is not shown stale pre-check results as current.
- What happens when the configured listing page itself returns a 404 or redirects to the homepage? → This is logged as a fetch failure/anomaly (per User Story 3), not silently treated as "the homepage was the intended target."
- What happens when the fetch-history retention policy would delete history still needed to explain a currently-open coverage gap or source-health flag? → Retention policy preserves (or summarizes) enough history to justify current health/coverage state; raw log pruning does not erase the ability to explain "why is this source marked failing."
- What happens when persistent document storage itself becomes unavailable during an upload? → The upload fails visibly to the user with a retry option, rather than appearing to succeed and silently losing the file.

## Requirements *(mandatory)*

### Functional Requirements — Scheduled Monitoring (FR-MON)

- **FR-MON-1**: The system MUST re-check every `active` registry source automatically on a recurring schedule, without requiring a user-initiated search.
- **FR-MON-2**: Each monitoring run MUST fetch the source's configured listing page (falling back to the domain homepage only when no listing page is configured, per FR-MON-7) and compare the result against stored scholarship records for that source.
- **FR-MON-3**: Monitoring MUST detect, at minimum: newly published scholarships, changes to requirements/deadlines/funding on existing scholarships, scholarships that closed, scholarships that reopened after being closed, and records that have gone stale (freshness window elapsed without successful re-verification).
- **FR-MON-4**: Every change detected by monitoring MUST flow through the same ingestion pipeline (extraction, normalization, deduplication, verification) used by on-demand discovery — monitoring MUST NOT use a separate, divergent data path.
- **FR-MON-5**: Monitoring MUST be implemented as a deterministic, scheduler-driven workflow; it MUST NOT be implemented as a new autonomous agent.
- **FR-MON-6**: A given source MUST NOT be fetched concurrently by two overlapping monitoring runs.
- **FR-MON-7**: When a registry source has no configured listing page, monitoring (and discovery) MUST fall back to the domain homepage and MUST record that the fallback occurred.

### Functional Requirements — Re-Match & Alert (FR-ALERT)

- **FR-ALERT-1**: When monitoring detects a new or changed scholarship, the system MUST re-match the affected scholarship against the profiles of owners for whom it is or was previously relevant, using the same deterministic hard-constraint evaluation and guardrailed matching logic used by on-demand matching.
- **FR-ALERT-2**: Hard-constraint results produced during monitoring-triggered re-matching MUST NOT be computed or overridden by an LLM; this is the same deterministic matching path as on-demand matching, not a separate implementation.
- **FR-ALERT-3**: When re-matching produces a newly favorable or materially changed match outcome for an owner, the system MUST alert that owner.
- **FR-ALERT-4**: Alerts MUST support, at minimum, in-app and email delivery, and the alert-generation mechanism MUST remain extensible to additional delivery channels without requiring redesign (OD-8).
- **FR-ALERT-5**: An owner MUST be able to configure whether they receive alerts; disabling alerts MUST NOT prevent monitoring/re-matching/record updates from occurring, only suppress notification delivery to that owner.
- **FR-ALERT-6**: A detected change that does not affect any owner's eligibility, saved scholarships, or prior matches MUST NOT generate an alert.

### Functional Requirements — Source Health Visibility (FR-HEALTH)

- **FR-HEALTH-1**: Every monitoring (and discovery) fetch outcome MUST be recorded, including success/failure status, timestamp, and failure reason when applicable.
- **FR-HEALTH-2**: A failed fetch MUST NEVER be presented or interpreted as "no scholarships found" for that source; it MUST be visibly distinguished from a successful fetch that found zero changes.
- **FR-HEALTH-3**: The system MUST expose, per registry source, its current status (active/failing/disabled), last-checked time, and last-successful-check time.
- **FR-HEALTH-4**: A source that fails its last N consecutive fetch attempts (N configurable) MUST be flagged as failing/unhealthy in the health view.
- **FR-HEALTH-5**: On extraction failure (e.g., page structure changed), the system MUST preserve the prior good record rather than overwriting it with empty/null results, and MUST flag the source for re-check.
- **FR-HEALTH-6**: Failed fetches MUST be retried according to a configured retry/backoff policy before being marked as a persistent failure.

### Functional Requirements — Coverage Analytics (FR-COVAN)

- **FR-COVAN-1**: The system MUST expose measurable coverage derived from the source registry and fetch history: counts of registered, active, successfully-checked, and failed sources; last-checked time per source; countries, universities, and providers covered; count of newly discovered candidate sources; count of verified opportunities; and identified coverage gaps.
- **FR-COVAN-2**: Coverage analytics MUST present coverage as a measured snapshot, never as a claim of complete or 100% global coverage.
- **FR-COVAN-3**: Candidate (pending-approval) sources MUST be counted and displayed separately from approved/active sources in coverage analytics.
- **FR-COVAN-4**: Coverage analytics MUST reflect the most recent fetch-log state per source, not a cached or stale aggregate beyond a reasonable, documented refresh interval.

### Functional Requirements — Record Lifecycle States (FR-LIFECYCLE)

- **FR-LIFECYCLE-1**: Every scholarship record MUST carry exactly one lifecycle state from the fixed set: `newly_discovered`, `verified`, `unverified`, `updated`, `expired`, `closed`, `reopened`, `stale`, `source_unavailable`.
- **FR-LIFECYCLE-2**: A record whose configured freshness window elapses without a successful re-verification MUST transition to `stale`.
- **FR-LIFECYCLE-3**: A record whose source cannot be reached across the configured retry policy MUST transition to `source_unavailable`, distinct from `expired` or `closed` (which require confirmed content from the source, not merely a failed fetch).
- **FR-LIFECYCLE-4**: A record transitioning from `closed` back to listed-as-open MUST become `reopened`, not revert to `newly_discovered`.
- **FR-LIFECYCLE-5**: Lifecycle state transitions MUST be driven by monitoring/ingestion outcomes (FR-MON-3, FR-HEALTH-1) deterministically, not inferred by an LLM.

### Functional Requirements — Listing-Page Accuracy (FR-FETCH)

- **FR-FETCH-1**: When a registry source has a configured listing-page URL, both monitoring and on-demand discovery MUST fetch that configured URL rather than the domain homepage.
- **FR-FETCH-2**: A change to a source's configured listing-page URL MUST take effect on the next monitoring or discovery run against that source.
- **FR-FETCH-3**: Every fetch outcome record (FR-HEALTH-1) MUST include which URL was actually fetched, so listing-page-vs-homepage fetches are auditable.

### Functional Requirements — Candidate-Source Validation (FR-CANDVAL)

- **FR-CANDVAL-1**: A newly discovered candidate source MUST automatically undergo checks for reachability, extractability, and official-source signals before being presented to a human reviewer.
- **FR-CANDVAL-2**: Automated candidate-source check results MUST be visible to the human reviewer but MUST NOT, by themselves, promote a candidate to `active` status.
- **FR-CANDVAL-3**: A candidate source MUST remain non-authoritative (not usable as a fact source by discovery, monitoring, matching, or Q&A) until a human explicitly approves it, regardless of automated check outcomes.
- **FR-CANDVAL-4**: A human reviewer MUST be able to approve a candidate source that failed one or more automated checks, and MUST be able to reject a candidate source that passed all automated checks; automated checks inform but never override human judgment.

### Non-Functional Requirements — Production Hardening (NFR-HARDEN)

- **NFR-HARDEN-1**: The deployed product MUST serve all traffic over HTTPS; plain-HTTP requests MUST be redirected or rejected.
- **NFR-HARDEN-2**: The deployed product MUST enforce request rate limits; requests beyond the configured limit MUST be rejected with a clear response rather than degrading the service.
- **NFR-HARDEN-3**: External fetches (monitoring and discovery) MUST retry transient failures with backoff per a configured policy before being marked a persistent failure (ties to FR-HEALTH-6).
- **NFR-HARDEN-4**: The deployed product MUST emit structured logs and metrics sufficient to diagnose a monitoring run or API request after the fact without reproducing it.
- **NFR-HARDEN-5**: The product's data MUST be backed up, and recovery to a recent backup point MUST be demonstrable.
- **NFR-HARDEN-6**: Uploaded documents MUST be stored such that they survive a redeploy of the application (not stored only on ephemeral local disk tied to a single deployed instance).
- **NFR-HARDEN-7**: A retention policy for the fetch-history log MUST be documented and enforced, balancing storage growth against the need to explain current source-health and coverage-gap state.
- **NFR-HARDEN-8**: The deployed product MUST apply general security hardening beyond the per-user data isolation already verified in Phase 4 (T132) — at minimum: validate and sanitize all externally-sourced content before it is stored or displayed (including content fetched from registry sources), manage all secrets/credentials outside of source control, and keep dependencies free of known critical vulnerabilities. This MUST NOT weaken or replace the existing per-user authorization scoping.

### Key Entities

- **Monitoring Run**: One scheduled execution of the source-monitoring workflow across some or all active registry sources; carries start/end time, sources processed, and summary outcome counts.
- **Source Fetch Outcome**: The result of one fetch attempt against one registry source (by monitoring or discovery), carrying source, URL actually fetched, status (ok/fail/timeout), timestamp, error detail, and retry count — the basis for both health visibility and coverage analytics.
- **Alert**: A notification generated for one owner describing a specific detected scholarship change (new match, changed eligibility, changed details), carrying delivery channel(s), scholarship reference, and what changed.
- **Candidate Source Validation Result**: The recorded outcome of automated reachability/extractability/official-signal checks for one candidate source, shown to a human reviewer alongside the existing candidate-source approval workflow.
- **Coverage Snapshot**: A point-in-time measurable summary derived from the source registry and fetch history — registered/active/checked/failed counts, countries/universities/providers covered, candidate count, verified-opportunity count, and coverage gaps.
- **Scholarship Lifecycle State**: The current status of a scholarship record within the fixed vocabulary (`newly_discovered`/`verified`/`unverified`/`updated`/`expired`/`closed`/`reopened`/`stale`/`source_unavailable`), carried on every scholarship record.
- **Retention Policy Record**: The documented, enforced rule governing how long fetch-history entries are kept before pruning or archival.

### Assumptions

- This feature extends the registry, ingestion pipeline, matching capability, and scholarship record model already specified in `specs/001-scholarship-mvp/`; it does not redefine them, and no change to `specs/001-scholarship-mvp/` is made or required by this spec.
- "Owner" refers to the individual-applicant persona already defined as the MVP's primary persona (001 spec, PRD OD-7); multi-tenant/consultant alerting is out of scope here.
- Exact monitoring schedule frequency, per-source freshness windows, retry-policy parameters (N retries, backoff timing), rate-limit thresholds, and fetch-history retention duration are operational configuration (consistent with PRD OD-4), not fixed numeric values defined in this spec.
- "Official-source signals" for candidate validation (e.g., government/institutional domain patterns, stated affiliation) are heuristic assistive signals for the human reviewer, not a substitute for human judgment, per constitution Principle IV.
- Additional alert channels beyond in-app and email (e.g., push, SMS, webhook) are explicitly future work; this spec only requires the alert-generation mechanism not preclude them.
- Live, autonomous external application submission remains out of scope (constitution Principle III); this feature does not introduce any submission capability.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A scheduled monitoring run, triggered end-to-end against a source with a known content change, results in the affected scholarship record being updated and the affected owner receiving an alert (in-app and email) within one monitoring cycle, with no manual/on-demand action required.
- **SC-002**: 100% of recorded fetch outcomes during monitoring and discovery are classified as either a success (with change count) or a failure (with reason) — zero fetch failures are presented or interpreted as "no scholarships found."
- **SC-003**: The coverage view's registered/active/checked/failed counts and country/provider coverage match the actual registry and fetch-history state with zero discrepancy when checked against seeded test data.
- **SC-004**: 100% of scholarship records display exactly one of the nine defined lifecycle states at all times, and records whose freshness window has elapsed are flagged `stale` within one monitoring cycle of the window elapsing.
- **SC-005**: 100% of monitoring and discovery fetches against a source with a configured listing page target that listing page (verified via logged fetch URL), not the domain homepage.
- **SC-006**: Zero candidate sources become usable as an authoritative fact source without an explicit, recorded human approval action, regardless of automated validation check results.
- **SC-007**: The deployed endpoint serves 100% of traffic over HTTPS, and a burst of requests exceeding the configured rate limit is rejected without degrading service availability for other clients.
- **SC-008**: An uploaded document remains retrievable after a simulated redeploy, with zero document loss across the redeploy event.
- **SC-009**: The fetch-history retention policy is documented and demonstrably enforced — entries older than the documented retention period are pruned or archived, verifiable by inspection after the policy's configured period.
- **SC-010**: Zero instances of unsanitized externally-sourced content reach storage or display, zero secrets/credentials appear in source control, and dependency vulnerability scans report zero known-critical vulnerabilities, verifiable by inspection.
