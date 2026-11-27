# Feature Specification: LLM Quota Governance & Cheap Monitoring

**Feature Branch**: `003-quota-governance`
**Created**: 2026-10-25
**Status**: Draft; spec, plan and tasks brought up to date with the 2026-10-26 audit and the code after feature 010 on 2026-11-26. Every change below is marked (updated 2026-11-26, post-audit/post-010).
**Input**: User description: "LLM quota governance and cheap monitoring for the Scholarship AI Assistant. Keep within the Gemini free tier (observed 20 requests/day for gemini-2.5-flash, per ADR-0012). Central LLM budget with priorities and 429 back-off; content-hash skip and conditional GET so unchanged pages cost no LLM call; per-source check cadence driven by update_frequency; provider errors recorded in source_fetch_log; deterministic extraction rules first with the LLM as fallback; budget visible in metrics; log the monitoring interval at scheduler startup. Grounded in docs/blueprint-gap-analysis.md (M-36, M-35, M-23, M-33), ADR-0012 and the Blueprint."

**Source of truth**: `docs/blueprint-gap-analysis.md` (M-36 monitoring cost and cadence, M-35 change detection, M-23 failure handling, M-33 per-source freshness, §5 quota model, §6 feature 003), `adr/0012-free-tier-stance-and-source-expansion-priority.md` (stay on the free tier; 003 is mandatory and built first), `specs/scholarship-ai-assistant-master-blueprint-v1.md` (§33 Failure & Retry, §35 Continuous Monitoring, §25.1 Evals) and `specs/scholarship-ai-assistant-prd-v1.md` (NFR-REL-1, NFR-REL-2, NFR-REL-3, FR-VERIFY-6, KPI-3, KPI-9), plus the project constitution (Principles I, IV, V). (updated 2026-11-26, post-audit/post-010): also `docs/full-project-audit.md` rows B-25, B-55, B-57, B-176, B-177, V-12, T-05, BR-02, P4-OF-2, P4-OF-3, P4-NZ-1 and the section 7 "Error taxonomy: provider failure" row, `specs/010-security-hardening/spec.md` (operator role, redirect-safe fetch, least-privilege database role), ADR-0015, ADR-0016, Blueprint v1.9 §22 (003 row of the 2026-10-27 table) and PRD v1.3 (NFR-REL-4..6, FR-VERIFY-9). This spec layers on `specs/001-scholarship-mvp/` and `specs/002-source-monitoring-hardening/` without modifying either, and it does not change what any scholarship record means (that is ADR-0013 and feature 004).

## Why this feature exists

The product depends on a free LLM allowance of roughly 20 requests per day (observed for the current model). Scheduled monitoring alone is estimated to need about 48 requests per day (2 monitored sources checked 24 times a day, each check calling the LLM even when the page has not changed). A real quota-exhausted error from the provider was recorded in production on 2026-10-24. Any feature added after this one (record backfill, source expansion, matching, drafting) adds more LLM load, so quota must be governed before anything else is built.

(updated 2026-11-26, post-audit/post-010) Facts as of 2026-11-26, from the audit and the code after feature 010:

- Feature 010 is built and deployed (merged to `main` 2026-11-02, deployed 2026-11-20, eight of eight post-deploy checks passed). Next in the build order is this feature (Blueprint v1.9 §22). Nothing in 003 is implemented: no `llm_gate.py`, no budget, no cadence (B-25).
- Monitoring is still on the **360-minute stopgap** set on 2026-10-25 and still calls the LLM for every source on every tick, unchanged page or not (B-177). The estimate of about 48 requests a day is for the old 60-minute tick; on the stopgap it is about 8 (estimate), and it returns to about 48 the moment the interval is shortened.
- Monitoring and discovery do not spend the allowance on extraction alone: every newly ingested candidate also goes through `classify`, which makes **about one more LLM call per new candidate** because funding and several other dimensions rarely resolve by rules (T-05; BR-02 measured one call for each of three realistic candidates under mocks, not on real pages). `classify` swallows an LLM failure silently (`tools/classify.py:140`).
- The cross-run extraction cache meant to protect the allowance almost never hits: ingestion writes its own `source_fetch_log` rows after the extraction row and they defeat the cache, so each `POST /discovery` re-fetches and re-extracts every discovery source (P4-OF-3).
- Nothing counts requests, nothing separates urgent work from background work, nothing skips unchanged pages. An LLM failure inside discovery leaves no trace at all: the exception is caught into `fetch_errors`, which the API drops (P4-OF-2); in monitoring it is recorded as a failed site fetch and counts toward the source's failure streak (V-12, B-176).
- Every LLM route except document upload lets a provider error escape as an HTTP 500 (B-57, section 7 "Error taxonomy: provider failure"). `doc_pipeline` is the only code that catches the provider's error type.
- The model names are constants in `core/llm.py` (B-55). Changing the model stays a feature 006 decision (ADR-0012).
- Since 010, starting a monitoring run and reading a run or a source's fetch log are operator-only; the redirect-safe fetch (`safe_get`) is the only fetch path; the application and the tests run as the least-privilege role `scholarship_app` and migrations as the owner.

## User Scenarios & Testing *(mandatory)*

The "users" here are the **owner/operator** who runs the system and the **student** whose requests must keep working. Stories are ordered by how much of the quota problem each one removes.

### User Story 1 - Unchanged pages cost no LLM call (Priority: P1)

When monitoring re-checks a source and its page has not meaningfully changed since the last successful extraction, the system recognises that and makes no LLM request. The check is still recorded as a successful check with zero changes, so a quiet source is never confused with a failing one. Where a source supports it, the system also avoids downloading the page again.

**Why this priority**: This is the single largest saving. Most monitored pages (annual and weekly programmes) rarely change, so the current behaviour spends almost the entire daily allowance re-reading identical pages.

**Independent Test**: Run two consecutive monitoring checks against a fixture source whose content does not change: the second check must make zero LLM requests and be recorded as "checked, unchanged". Then change the fixture's real content: the next check must make exactly one LLM request and apply the change through the normal pipeline.

**Acceptance Scenarios**:

1. **Given** a source whose last extraction succeeded and whose page content is identical on the next check, **When** monitoring checks it, **Then** no LLM request is made, the fetch history records a successful "unchanged" outcome, and the source's last-checked and last-success times advance.
2. **Given** a source whose page differs only in non-content noise (scripts, styles, whitespace, timestamps in markup), **When** monitoring checks it, **Then** it is treated as unchanged.
3. **Given** a source whose real listing content has changed, **When** monitoring checks it, **Then** exactly one extraction is performed and the result flows through the existing ingestion pipeline unchanged (no divergent data path).
4. **Given** a source that has never been successfully extracted, **When** it is first checked, **Then** extraction is performed (there is no baseline to compare against).
5. **Given** a source whose server supports conditional requests and reports the page unmodified, **When** monitoring checks it, **Then** the page is not re-downloaded and the outcome is recorded as unchanged.
6. **Given** a page that is unchanged but whose last extraction attempt failed (provider error or zero grounded candidates), **When** monitoring checks it, **Then** the failure is not mistaken for "unchanged success": the extraction is retried, but with bounded back-off so a permanently unextractable page does not consume the allowance every check.
7. **Given** a discovery source that was extracted within its freshness interval and whose ingestion has since written its own log rows, **When** a student runs discovery again, **Then** the earlier extraction still counts: no re-fetch and no LLM request (updated 2026-11-26, post-audit/post-010) (FR-SKIP-8, P4-OF-3).

---

### User Story 2 - A central daily LLM budget protects user-facing work (Priority: P1)

Every request to the LLM provider passes through one budget that knows the daily allowance, how much has been used, and how much is reserved for students. Work is classed by priority: user-facing requests first, then one-off backfill jobs, then scheduled monitoring, then candidate-source validation. When the remaining allowance falls to the reserve, background work is deferred (not failed) and a student's question or document upload still has allowance left. When the provider itself reports quota exhaustion, the system stops calling it until the provider's suggested retry time instead of hammering it.

**Why this priority**: Without this, background monitoring starves students (the allowance is gone before they ask anything) and every new feature competes unpredictably for the same 20 requests.

**Independent Test**: With a fixture provider and a small configured allowance, let background work consume everything it is allowed to; verify the reserved amount is still available to user-facing requests, that background requests beyond the line are deferred and counted, and that a simulated provider quota error pauses all non-user-facing work until the stated retry time.

**Acceptance Scenarios**:

1. **Given** a daily allowance of N and a reserve of R for user-facing work, **When** background work has used N − R requests, **Then** further background requests are deferred and recorded as deferred, while user-facing requests are still allowed up to R more.
2. **Given** the allowance is fully spent, **When** a student makes an LLM-backed request, **Then** they receive a clear "temporarily unavailable, try again after <time>" response, not a server error, not an empty answer, and not a misleading "unknown" or "not eligible" result, and no provider call is made.
3. **Given** the provider returns a quota-exhausted error with a suggested retry delay, **When** any non-user-facing request is next attempted before that time, **Then** it is deferred without calling the provider.
4. **Given** the provider reports exhaustion earlier than the local count predicted (because another consumer shares the same key, such as a developer's laptop or a test run), **When** that happens, **Then** the provider's signal is treated as authoritative: background work stops for the stated period and the discrepancy is visible in the metrics.
5. **Given** a large one-off job (for example re-reading 34 records after a later feature), **When** it runs under the backfill class, **Then** it drains across several days within its per-day share and never takes allowance from the user-facing reserve.
6. **Given** a new LLM-backed operation is added in future, **When** it is invoked without declaring a priority class, **Then** it is refused rather than silently run unbudgeted.

---

### User Story 3 - Each source is checked as often as it actually changes (Priority: P2)

Today every monitored source is checked on every scheduler tick regardless of how often it changes. After this feature, the scheduler tick only asks "which sources are due?", and a source becomes due again after an interval derived from its declared update frequency: an annual programme list is checked about weekly, a weekly one about daily. An operator can override the interval per source, and can still trigger a manual run on demand.

**Why this priority**: Even with unchanged-page skipping, a cheaper fetch still costs time and load, and a tick that checks everything makes cost depend on the tick length. Per-source cadence makes cost depend on the registry, not on a global timer. It ranks below the first two because skipping already removes most LLM spend.

**Independent Test**: Configure one source as annual and one as weekly, simulate 14 days of ticks, and count checks: the annual source is checked about twice, the weekly source about 14 times, and neither goes stale before its freshness window.

**Acceptance Scenarios**:

1. **Given** a source with a declared update frequency, **When** the scheduler tick runs, **Then** the source is checked only if its own interval since the last successful check has elapsed.
2. **Given** a source with no declared update frequency, **When** it is scheduled, **Then** a conservative daily default applies and this is visible to the operator.
3. **Given** an operator override of a source's check interval, **When** the next tick runs, **Then** the override is used instead of the derived interval.
4. **Given** any derived or overridden interval, **When** it would exceed the source's freshness window, **Then** the system shortens it or flags the conflict so that records never turn stale solely because of the schedule.
5. **Given** several sources due at once and not enough budget for all, **When** the run starts, **Then** the stalest source is checked first and the rest are deferred to the next tick, so no source is starved indefinitely.
6. **Given** a manual on-demand monitoring run, **When** the operator triggers it, **Then** it checks all due sources and still obeys the budget (updated 2026-11-26, manager decisions): the 002 route and its body are unchanged, so the operator cannot name sources.

---

### User Story 4 - Provider failures are recorded, never silent (Priority: P2)

When an LLM request fails during extraction or candidate validation (quota exhausted, provider outage, malformed response), the outcome is recorded against that source in the fetch history with a distinct reason class, in discovery as well as in monitoring. A provider-side failure never marks the source itself as failing, because the source did nothing wrong.

**Why this priority**: Today a provider error in discovery is only written to the application log, and a quota error in monitoring is indistinguishable from a site problem in the health view. This violates the project rule that a failed retrieval must never read as "no scholarships found".

**Independent Test**: Inject a quota error, a provider outage and an unparseable response into extraction for a fixture source; verify three distinct recorded outcomes, no change to the source's health status, and no record updates caused by the failures.

**Acceptance Scenarios**:

1. **Given** a quota-exhausted error during extraction, **When** the check completes, **Then** the fetch history records a deferred/quota outcome (not a site failure), the source's consecutive-failure streak is unchanged, and the check is retried after the stated delay.
2. **Given** a provider outage or malformed response, **When** the check completes, **Then** the fetch history records a distinct provider-error outcome with a short non-secret reason.
3. **Given** the same failures occur on the discovery path, **When** discovery completes, **Then** they are recorded the same way, not only logged.
4. **Given** any such failure, **When** records are evaluated, **Then** no existing scholarship is closed, expired, overwritten with empty data, or marked source-unavailable because of it.
5. **Given** the health view, **When** a source has recent provider-side failures, **Then** they are shown separately from site fetch failures.
6. **Given** any LLM-backed route (match-url, match, Q&A, discovery, CV/SOP generation, CV import, documents), **When** the provider fails for any reason (quota, outage, unparseable response, not configured), **Then** the student gets the same clear temporary-unavailable response as in story 2, never HTTP 500 (updated 2026-11-26, post-audit/post-010) (FR-ERR-6, B-57).

---

### User Story 5 - Deterministic extraction first, the LLM only as a fallback (Priority: P3)

For sources whose pages have a stable structure, the operator can configure source-specific extraction rules. When the rules extract grounded results, no LLM request is made at all. If the rules stop matching (the site changed its layout), the system falls back to the LLM within budget and flags the source's rules as needing attention, rather than silently returning nothing.

**Why this priority**: It drives the cost of stable sources to zero, but it needs per-source work and a safe fallback path, so it follows the general savings in stories 1 to 4.

**Independent Test**: Give a fixture source a rule set that matches its markup: extraction succeeds with zero LLM requests. Alter the markup so the rules no longer match: the next check falls back to one LLM request and flags the rules for attention.

**Acceptance Scenarios**:

1. **Given** a source with extraction rules that match its page, **When** it is checked, **Then** candidates are extracted without any LLM request.
2. **Given** deterministic results, **When** they are accepted, **Then** they pass the same grounding checks as LLM results (no fabrication, constitution Principle I).
3. **Given** rules that match nothing, **When** the source is checked, **Then** the LLM fallback runs once (budget permitting) and the source is flagged for rule review.
4. **Given** rules that produce a result differing sharply from the previous extraction (for example far fewer items), **When** accepted, **Then** the anomaly is flagged for the operator instead of being applied silently.
5. **Given** at least one currently monitored source, **When** this feature ships, **Then** that source runs on deterministic rules in normal operation.

---

### User Story 6 - Budget and cadence are visible (Priority: P2)

The operator can see at any time how much of today's allowance has been used, by priority class, how much remains, how many requests were deferred or skipped as unchanged, how often the provider reported exhaustion, and when the allowance resets. At scheduler startup, the system also logs the effective monitoring interval, the per-source cadence rules in force, and the budget settings, so the running configuration is never a guess.

**Why this priority**: The stopgap interval change on 2026-10-25 could only be proved indirectly because the running system does not state its own interval. Quota governance that cannot be observed cannot be trusted or tuned.

**Independent Test**: After a mixed day of fixture activity, read the metrics and confirm the counts match the activity; restart the service and confirm the startup log states the interval, cadence rules and budget settings.

**Acceptance Scenarios**:

1. **Given** LLM requests of several classes during the day, **When** the operator reads the metrics, **Then** used, remaining, reserved, deferred, skipped-unchanged and provider-exhaustion counts are shown, split by priority class.
2. **Given** the scheduler starts, **When** the startup log is read, **Then** it states the monitoring tick interval, the per-frequency cadence defaults, the allowance, the reserve and the day boundary, and no secret value.
3. **Given** a source that "changes" at almost every check, **When** the operator reviews the metrics, **Then** it is flagged as noisy so its change detection can be tuned.
4. **Given** a later change of allowance (for example after a paid-tier decision at feature 006), **When** the configuration is updated, **Then** the budget and metrics follow without code changes.
5. **Given** the operator-only budget and cadence view, **When** a signed-in non-operator asks for it, **Then** the answer is 403; an anonymous caller gets 401; an operator gets the numbers (updated 2026-11-26, post-audit/post-010) (FR-OBS-5).

---

### Edge Cases

- **Several workers or restarts**: usage is counted once, shared across all workers and surviving restarts; a restart does not reset the day's count and two workers never double-spend the allowance.
- **Budget state unavailable**: if the system cannot read or write the budget count, background LLM work pauses (fail safe) and user-facing work continues with the provider's own limit as the backstop.
- **Shared key outside the system**: laptop development, manual testing and live smoke tests use the same key and cannot be seen by the local count; the provider's exhaustion signal is the authority (see Story 2, scenario 4).
- **Day boundary**: the allowance resets at the provider's day boundary (observed as midnight US Pacific time), not at the server's local midnight.
- **Retries cost allowance**: every retried request counts; retries must not turn one logical operation into several budget-free calls.
- **Persistently unextractable page**: a page that is unchanged and keeps yielding zero grounded results (for example a source currently marked failing) is retried with growing back-off and stops until the page changes or the operator forces a re-check.
- **Noise defeating change detection**: a page that changes on every fetch for non-content reasons would remove the saving; such a source is flagged as noisy and its fingerprint normalisation can be tuned.
- **Budget exhausted mid-run**: the run records which sources were checked and which were deferred, marks itself partial rather than failed, and the next run starts with the deferred ones.
- **Conditional-request mismatch**: a server that claims "not modified" while the content has changed is caught by the periodic forced full check described in the requirements.
- **Records going stale**: a long cadence combined with deferrals must not cause records to become stale before their freshness window; the system flags the risk rather than letting it happen silently.
- **Manual runs and discovery**: on-demand monitoring runs and student-triggered discovery draw from the same budget as scheduled work, in their own priority class. (updated 2026-11-26, post-audit/post-010): since 010 only an operator can start a manual run; discovery stays open to any signed-in user.
- **Classification spends the allowance too** (updated 2026-11-26, post-audit/post-010): a first run or a changed page that yields N new candidates costs up to N more `classify` requests (T-05, BR-02). They pass the same gate under the caller's class, so a deferred or refused classify call is counted and is no longer silent; what a record stores when classify gets no LLM answer is unchanged, and the record is queued for a later attempt (FR-BUDGET-13) (updated 2026-11-26, manager decisions).
- **Paid tier later**: a much larger allowance must work with no code change; the guard simply has more headroom.

## Requirements *(mandatory)*

### Functional Requirements: Central budget (FR-BUDGET)

- **FR-BUDGET-1**: Every request to the LLM provider (generation and embedding alike) MUST pass through one central budget; no LLM-backed operation may bypass it.
- **FR-BUDGET-2**: The budget MUST know a configurable daily allowance per request type, the amount used today, and a configurable reserve for user-facing work, with the day boundary configurable and defaulting to the provider's observed reset time.
- **FR-BUDGET-3**: Every LLM-backed operation MUST declare exactly one priority class from the fixed set: user-facing, one-off backfill, scheduled monitoring, candidate validation. A call without a class MUST be refused.
- **FR-BUDGET-4**: When the remaining allowance is at or below the reserve, requests of the three background classes MUST be deferred (not failed) and counted; user-facing requests MAY use the reserve.
- **FR-BUDGET-5**: When the allowance is fully spent or the provider reports exhaustion, a user-facing request MUST return a clear temporary-unavailable response that states when to retry; it MUST NOT return a server error, an empty result, or a misleading "unknown" or "not eligible" outcome, and MUST NOT call the provider.
- **FR-BUDGET-6**: When the provider reports quota exhaustion, the system MUST stop issuing non-user-facing requests until the provider's suggested retry time (or a safe default if none is given), and MUST NOT retry in a tight loop.
- **FR-BUDGET-7**: The provider's exhaustion signal MUST take precedence over the local count; a discrepancy MUST be recorded and visible (FR-OBS-1).
- **FR-BUDGET-8**: Usage MUST be counted exactly once across multiple workers and MUST survive restarts.
- **FR-BUDGET-9**: If the budget state cannot be read or written, background LLM work MUST pause rather than proceed unbudgeted.
- **FR-BUDGET-10**: One-off backfill work MUST be able to drain over several days within a per-day share, without ever consuming the user-facing reserve.
- **FR-BUDGET-11**: The allowance, reserve and day boundary MUST be changeable by configuration alone, so a future paid-tier decision (ADR-0012, feature 006) needs no code change.
- **FR-BUDGET-12** (updated 2026-11-26, post-audit/post-010): The generation and embedding model names MUST come from configuration, with defaults equal to today's names (`models/gemini-2.5-flash`, `models/gemini-embedding-001`), so nothing changes unless an operator sets them. Changing the model is NOT part of this feature; ADR-0012 keeps that decision at feature 006. The embedding vector size stays fixed (768) because stored vectors depend on it. *(Traced to audit B-55.)*
- **FR-BUDGET-13** (updated 2026-11-26, manager decisions): When a `classify` LLM call is refused by the gate or fails on the provider side, the record MUST be stored exactly as today (dimensions the rules could not resolve stay `value=None` with confidence "unknown"; nothing is guessed) AND the record MUST be added to a small pending-classification queue. A scheduler step MUST drain the queue under the backfill class when the budget admits it, re-run classify for that record's text, and fill ONLY dimensions that are still unknown; it MUST NOT overwrite a known value. Each attempt MUST be logged, and the queue size MUST be shown in the operator budget view (FR-OBS-1, FR-OBS-5). The queue is its own table (record id, queued at, attempts, last reason), not a column on scholarships, so what a record means stays a feature 004 matter (ADR-0013). *(Manager decision 2026-11-26 on audit T-05/BR-02; PRD NFR-REL-4 "defer, not fail" and NFR-REL-6.)*

### Functional Requirements: Change detection (FR-SKIP)

- **FR-SKIP-1**: For each monitored source the system MUST keep a fingerprint of the page content from the last successful extraction, computed so that markup noise (scripts, styles, whitespace, boilerplate timestamps) does not change it.
- **FR-SKIP-2**: When the new fingerprint equals the stored one and the last extraction succeeded, the system MUST skip the LLM request and record a successful "unchanged" outcome that advances last-checked and last-success.
- **FR-SKIP-3**: Where a source supports conditional requests, the system MUST use them to avoid re-downloading unchanged pages, and MUST fall back to the fingerprint when they are unsupported.
- **FR-SKIP-4**: An unchanged page whose last extraction failed MUST NOT be treated as an unchanged success; it is retried with bounded back-off (see edge cases).
- **FR-SKIP-5**: The system MUST periodically force a full re-extraction of a source regardless of fingerprint (an operator-configurable interval, bounded by the source's freshness window) so that a wrong "unchanged" signal cannot hide a change indefinitely.
- **FR-SKIP-6**: A skipped check MUST NOT alter any scholarship record, lifecycle state, or alert, and MUST NOT produce spurious change records.
- **FR-SKIP-7**: A source whose fingerprint changes at almost every check MUST be flagged as noisy in the metrics.
- **FR-SKIP-8** (updated 2026-11-26, post-audit/post-010): The cross-run extraction cache (the "extracted recently, answer from the stored extraction" check used by discovery) MUST be driven by the source's own listing-extraction rows, so that rows ingestion writes for individual records (success rows with a record count but no fetched URL) cannot defeat it. After one extraction, any number of ingestion rows MUST NOT turn a later discovery call within the freshness interval into a re-fetch or a new LLM request. *(Traced to audit P4-OF-3.)*

### Functional Requirements: Per-source cadence (FR-CADENCE)

- **FR-CADENCE-1**: The scheduler tick MUST only determine which sources are due; the LLM cost of a tick MUST NOT depend on the tick length.
- **FR-CADENCE-2**: A source's next-due time MUST derive from its declared update frequency, with documented defaults per frequency and a conservative daily default when none is declared.
- **FR-CADENCE-3**: An operator MUST be able to override a source's check interval; the override takes precedence.
- **FR-CADENCE-4**: A source's interval MUST NOT exceed its freshness window; if it would, the system MUST shorten it or flag the conflict.
- **FR-CADENCE-5**: When fewer sources can be checked than are due (budget or time), the stalest MUST be checked first and the remainder deferred to the next tick.
- **FR-CADENCE-6**: Monitoring MUST remain a deterministic scheduler-driven workflow (not an agent), and the existing overlap protection (no source checked concurrently by two runs) MUST be preserved.

### Functional Requirements: Provider errors (FR-ERR)

- **FR-ERR-1**: A failure of an LLM request during extraction or candidate validation MUST be recorded in the source's fetch history with a distinct reason class (quota, provider error, unparseable response), on the monitoring path and on the discovery path alike.
- **FR-ERR-2**: A provider-side failure MUST NOT increment the source's consecutive-failure streak or change its health status.
- **FR-ERR-3**: A provider-side failure MUST NOT modify, close, expire or empty any existing scholarship record, and MUST NOT be presented as "no scholarships found" (PRD FR-VERIFY-6).
- **FR-ERR-4**: Recorded reasons MUST be short and MUST NOT contain secrets, credentials, or page content.
- **FR-ERR-5**: The health view MUST show provider-side failures separately from site fetch failures.
- **FR-ERR-6** (updated 2026-11-26, post-audit/post-010): Every route that reaches the LLM (match-url, match, Q&A, discovery, CV and SOP generation, CV import, documents) MUST answer ANY provider failure (quota, outage, unparseable response, provider not configured) with the same clear temporary-unavailable response as FR-BUDGET-5 (HTTP 503, a retry time and a short non-secret reason; shape in `contracts/api-delta.md`), never HTTP 500. The document-upload route keeps its existing answer for this case, explicitly: HTTP 201 with `extraction_unavailable` (the upload fix); that is also a clear temporary-unavailable answer and never a 500 (updated 2026-11-26, manager decisions). *(Traced to audit B-57 and the section 7 "Error taxonomy: provider failure" row.)*

### Functional Requirements: Deterministic extraction (FR-RULES)

- **FR-RULES-1**: An operator MUST be able to attach source-specific extraction rules to a registry source; extraction by rules MUST be attempted before any LLM request for that source.
- **FR-RULES-2**: Results from rules MUST pass the same grounding and validation as LLM results (constitution Principle I).
- **FR-RULES-3**: When rules match nothing, the system MUST fall back to the LLM (subject to the budget) and flag the rules for review; it MUST NOT silently return an empty result.
- **FR-RULES-4**: A rules result that departs sharply from the previous extraction MUST be flagged for the operator before it is applied.
- **FR-RULES-5**: Extraction rules are human-defined governed configuration; the LLM MUST NOT author or activate rules on its own (constitution Principle IV).
- **FR-RULES-6** (updated 2026-11-26, manager decisions): The operator command that sets rules or the check interval (`set`) MUST write a feature 010 audit row: action `source.rules.set`, `user_id` = the operator named by a required `--operator-email` argument, entity `source:<uuid>`. The command MUST refuse (and change nothing) if that email does not belong to an existing operator. See deviation D-1.

### Functional Requirements: Observability (FR-OBS)

- **FR-OBS-1**: The system MUST expose, to the operator, for the current budget day: requests used and remaining by priority class and request type, the reserve, deferred count, skipped-unchanged count, provider-exhaustion count, local-versus-provider discrepancies, and the reset time; (updated 2026-11-26, manager decisions) also the number of records waiting in the pending-classification queue (FR-BUDGET-13).
- **FR-OBS-2**: At scheduler startup the system MUST log the effective monitoring tick interval, the per-frequency cadence defaults and any per-source overrides, and the budget settings (allowance, reserve, day boundary), without logging any secret.
- **FR-OBS-3**: Each monitoring run's summary MUST state how many sources were checked, skipped as unchanged, deferred for budget, and failed, so the cost of a run is explainable after the fact.
- **FR-OBS-4**: Metrics access control MUST NOT be weakened by this feature. (updated 2026-11-26, post-audit/post-010): today `/metrics` needs the metrics bearer token and is not exposed through Caddy (010 post-deploy check 6); both stay as they are.
- **FR-OBS-5** (updated 2026-11-26, post-audit/post-010): The budget and cadence view (the FR-OBS-1 numbers (including the classification queue size) plus each source's interval, last check, next due time, noisy flag and rules-review flag) MUST be available to an operator through an API route that uses the existing `require_operator` dependency from feature 010: a signed-in non-operator gets 403 and an anonymous caller 401. It is read-only. *(Owner decision 2026-11-26.)*

### Process rules (Blueprint §23) (updated 2026-11-26, post-audit/post-010)

- **FR-PROC-1 (entry-point rule)**: Every capability in this feature MUST be reachable from an API route, the scheduler, application start or an operator CLI command, and MUST have a test that starts from that entry point (map below). No capability may exist only as an internal helper.
- **FR-PROC-2 (deviation stop rule)**: If implementation finds anything that differs from the Blueprint, the PRD or an ADR, work MUST stop and the owner MUST decide, with the decision recorded in both.

#### Entry-point map (FR-PROC-1) (updated 2026-11-26, post-audit/post-010)

| Capability | Entry point | Test starts at |
|---|---|---|
| Budget gate admits or refuses every LLM call (FR-BUDGET-1..11) | every LLM route, the scheduler tick, the operator CLI `record-snapshot` | those entry points with a fake gate and fake provider |
| Temporary-unavailable answer on LLM routes; provider failure never 500 (FR-BUDGET-5, FR-ERR-6) | the eight routes: `POST /profile/import-cv`, `/scholarships/match-url`, `/scholarships/{id}/match`, `/scholarships/{id}/qa`, `/discovery`, `/applications/{id}/documents`, `/applications/{id}/generate/cv`, `/applications/{id}/generate/sop` | each route, per failure kind |
| Unchanged-page skip, forced re-check, back-off (FR-SKIP-1..7) | scheduler tick; `POST /monitoring/runs` (operator) | the tick function; the route as operator |
| Cross-run extraction cache survives ingestion rows (FR-SKIP-8) | `POST /discovery` | the route, after ingestion has written its rows |
| Per-source cadence and cheap tick (FR-CADENCE-1..6) | scheduler tick; `POST /monitoring/runs` | the tick function; the route |
| Provider failures recorded, kept out of health (FR-ERR-1..5) | scheduler tick, manual run, `POST /discovery`, candidate validation inside the run | those entry points with an injected provider failure |
| Provider-failure fields in the health views (FR-ERR-5) | `GET /sources/health`, `GET /sources/{id}/fetch-log` | those routes |
| Run counters (FR-OBS-3) | `GET /monitoring/runs`, `GET /monitoring/runs/{id}` | those routes |
| Budget metrics and noisy-source gauge (FR-OBS-1, FR-SKIP-7) | `GET /metrics` | the route with and without the token |
| Operator-only budget and cadence view (FR-OBS-5) | `GET /monitoring/budget` (approved 2026-11-26) | the route as operator, non-operator and anonymous |
| Startup log (FR-OBS-2) | application start (`start_scheduler`) | starting the app |
| Ledger pruning | the daily retention job | the scheduled job function |
| Pending-classification queue: enqueue on refusal, drain under the backfill class (FR-BUDGET-13) | the scheduler tick (drain step); `POST /discovery` and the monitoring run (enqueue, through ingestion) | the tick function; the route and the run, with a refusing fake gate |
| Rules command audit and operator check (FR-RULES-6) | operator CLI `python -m app.services.source_rules set` | the CLI `main` |
| Model names from configuration (FR-BUDGET-12) | application start, then a gated call | building `Settings`, then `generate_text` |
| Rules-first extraction inside a check (FR-RULES-1..4) (added by the T462 audit, 2026-11-27) | scheduler tick; `POST /monitoring/runs` | the tick function with an enabled rule set and a fake provider |
| Extraction rules: set, parity, diff, record-snapshot (FR-RULES-1..5) | operator CLI `python -m app.services.source_rules` | the CLI `main` |
| Deploy of 003 and its post-deploy checks | runbook section 16 on the VM | the post-deploy check list |

### Out of scope

- Switching to a paid tier or a different model (decided at feature 006 per ADR-0012).
- Changing what `verified` means, or following detail pages (feature 004; ADR-0013).
- Adding or enabling more monitored sources (feature 006).
- Caching student-facing answers or match results.
- Detecting changes to scholarship requirements (feature 004 and later).
- A dashboard (frontend is deferred); this feature exposes numbers, not a screen.
- Per-student quotas or billing (SaaS, deferred by PRD §30).
- (updated 2026-11-26, post-audit/post-010): Making manual monitoring runs operator-only, the redirect-safe fetch and the least-privilege database role (all delivered by 010; this feature only builds on them).
- (updated 2026-11-26, post-audit/post-010): Reducing the number of `classify` calls per new candidate (rules-first or batched classification) and fixing what `classify` stores when it gets no LLM answer (P4-NZ-1, P4-CT-3: data-quality work for features 004 and 006); 003 only gates, counts and defers those calls (FR-BUDGET-13) (updated 2026-11-26, manager decisions).

### Key Entities

- **LLM Budget**: the day's allowance per request type, the reserve for user-facing work, the amount used per priority class, the day boundary, and any provider-imposed pause with its retry time.
- **Priority Class**: one of user-facing, one-off backfill, scheduled monitoring, candidate validation; declared by every LLM-backed operation.
- **Budget Event**: a recorded decision such as allowed, deferred (with reason), denied to a student, provider exhaustion observed, or local-versus-provider discrepancy.
- **Content Fingerprint**: per monitored source, a noise-insensitive summary of the page at the last successful extraction, plus any conditional-request validators and a reference to that extraction's outcome.
- **Check Schedule**: per source, its effective interval (derived or overridden), last check, next due time, and consecutive unchanged or noisy checks.
- **Provider Error Outcome**: a fetch-history entry whose reason class is quota, provider error or unparseable response, distinct from a site fetch failure.
- **Pending Classification Entry** (updated 2026-11-26, manager decisions): a record whose classify call was refused or failed, with when it was queued, how many attempts were made and the last reason; removed when it has been classified.
- **Extraction Rule Set**: operator-defined, per-source rules, with a review flag raised when they stop matching or produce an anomalous result.

## Assumptions

- The monitored set stays the two current web discovery sources (EACEA and HEC); this feature does not change which sources are monitored.
- The 20 requests per day figure is observed for one model on the free tier; the allowance is configuration, not a hard-coded constant.
- The monitoring scheduler tick may return to a short interval once cadence gating exists, because tick length no longer drives LLM cost. The VM currently runs the 360-minute stopgap set on 2026-10-25.
- The budget counts requests, not tokens, because that is how the observed limit is expressed.
- Embedding requests are counted and visible, but their provider limit is not assumed to equal the generation limit; the default allowance applies to generation only until an embedding limit is measured.
- Tests and CI for this feature use recorded or fake provider responses and make no live provider calls (constitution Principle V).
- Live smoke tests and developer use of the same key remain outside the local count; the provider's exhaustion signal covers them.
- (updated 2026-11-26, post-audit/post-010): The new tables work as the least-privilege role `scholarship_app`: migrations run as the owner, the role script (`backend/scripts/db/create_app_role.sql`) is re-run after them, and its default privileges grant the application SELECT, INSERT, UPDATE and DELETE on tables the owner creates (010, runbook section 14). No new database role and no DDL right for the application.
- (updated 2026-11-26, post-audit/post-010): Classification calls are part of the load this feature governs. The default allowance of 20 a day is unchanged; whether it is enough once classify and feature 004's re-extraction run is measured after deploy (SC-014), not assumed.

### Deviations from the Blueprint, PRD and ADRs (updated 2026-11-26, manager decisions)

- **D-1**: the rules `set` command is audited (FR-RULES-6). This extends PRD NFR-SEC-7's list of audited actions (sign-up, source approval and rejection, manual monitoring runs, submission approvals, operator grants) by one action, `source.rules.set`. The manager amends the PRD and the Blueprint when 003 closes; 010's closed audit action set gains the new action.
- **D-2**: FR-ERR-6 extends the temporary-unavailable answer of ADR-0014 decision 6 from "allowance spent or paused" to any provider failure on a user-facing route. ADR-0014 carries a dated amendment note (original text unchanged). The manager updates the Blueprint §22 003 row when 003 closes.

### Defaults chosen for your review

These are the places where I picked a reasonable default instead of asking. Change any of them and the requirements above still hold.

1. **Reserve**: 8 of the 20 daily requests are reserved for user-facing work, leaving up to 12 for background classes.
2. **Cadence defaults**: weekly sources are due daily; monthly, quarterly and annual sources are due weekly; undeclared is daily.
3. **Student behaviour when the allowance is spent**: fail fast with a "try again after <time>" message; no queuing.
4. **Forced full re-check**: at least every 7 days per source, and always within the source's freshness window.
5. **Back-off for an unchanged, unextractable page**: doubling, capped at the source's normal cadence interval.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a day when no monitored page changes, background LLM requests for the current registry total at most 4, down from an estimated 48 (measured over 7 days of operation).
- **SC-002**: In any 24-hour budget day, requests issued by this system never exceed the configured allowance, and no background request is issued while the remaining allowance is at or below the reserve.
- **SC-003**: 100% of monitoring checks against an unchanged, previously successful source make zero LLM requests and are recorded as successful "unchanged" checks.
- **SC-004**: Over a 14-day simulation, an annual source is checked at most 3 times and a weekly source at most 15 times, and no record becomes stale solely because of the schedule.
- **SC-005**: 100% of LLM provider failures during monitoring, discovery and candidate validation appear in the fetch history with a distinct reason, and 0% of them change a source's health status or modify an existing scholarship.
- **SC-006**: After the provider reports quota exhaustion, 0 further non-user-facing requests are made before the stated retry time, and a student request in that period receives a temporary-unavailable response with a retry time without any provider call.
- **SC-007**: On a day when background work has consumed everything it is allowed to, at least the reserved number of user-facing requests (8 by default) still succeed.
- **SC-008**: The operator can read today's used, remaining, deferred and skipped counts at any time, and the startup log of every restart states the monitoring interval, cadence defaults and budget settings.
- **SC-009**: A page whose only change is non-content noise triggers no extraction in 100% of fixture cases, and a real content change triggers extraction within one due check in 100% of cases.
- **SC-010**: At least one currently monitored source runs in normal operation on deterministic extraction with zero LLM requests when its rules match, and falls back to the LLM exactly once and flags itself when they stop matching.
- **SC-011** (updated 2026-11-26, post-audit/post-010): On each of the eight LLM-backed student routes, a simulated quota error, outage, unparseable response and missing key each produce the temporary-unavailable answer (503 with a retry time; 201 `extraction_unavailable` for document upload) and 0 responses of HTTP 500 (FR-ERR-6).
- **SC-012** (updated 2026-11-26, post-audit/post-010): After one extraction and any number of ingestion rows, a second discovery call within the freshness interval makes 0 fetches and 0 LLM requests in 100% of fixture cases (FR-SKIP-8).
- **SC-013** (updated 2026-11-26, post-audit/post-010): The budget and cadence view returns 403 to a signed-in non-operator, 401 to an anonymous caller and the numbers to an operator, in 100% of cases (FR-OBS-5).
- **SC-014** (updated 2026-11-26, post-audit/post-010): After deploy, the 7-day measurement behind SC-001 reports background requests per day next to the number of new candidates ingested that day (two read-only queries), so the share caused by classify calls is visible and not assumed.
- **SC-015** (updated 2026-11-26, post-audit/post-010): Every capability of this feature appears in the entry-point map with a test whose first call is its entry point (FR-PROC-1), and the tasks audit confirms it.
- **SC-016** (updated 2026-11-26, manager decisions): When classify is refused or fails for a new record, the record is stored unchanged with no dimension guessed and appears in the queue in 100% of fixture cases; once the budget admits, the drain step fills only dimensions still unknown and overwrites 0 known values (FR-BUDGET-13).
