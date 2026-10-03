# Phase 0 Research: Source Monitoring, Coverage Analytics & Production Hardening

Every decision below starts from what already exists in `backend/` (verified by reading the code, not the 001 docs alone) and only adds something new when a requirement in `specs/002-source-monitoring-hardening/spec.md` cannot be met with what is already there.

## Stack-reuse correction (read before anything else)

`specs/001-scholarship-mvp/plan.md` and the constitution describe the agent-orchestration dependency as "LangGraph + LangChain + the OpenAI Agents SDK." **That is not what is actually installed or imported.** `backend/pyproject.toml` has no `openai`/`openai-agents` dependency at all; the real LLM provider is **Google Gemini** via `google-genai` + `langchain-google-genai`, configured through `GEMINI_API_KEY` (`backend/app/core/config.py`). `langgraph`/`langchain` are real and used exactly as documented (e.g. `backend/app/workflows/ingestion/graph.py`, `backend/app/workflows/url_match/`).

This plan reuses the **actual** stack (LangGraph + LangChain + Gemini), not the aspirational "OpenAI Agents SDK" label. No scholarship-monitoring code path calls any LLM provider anyway (FR-MON-5, Principle II), so this correction doesn't change any design decision below — it only prevents this plan from introducing a dependency (`openai-agents`) that would be pure, unused weight. Flagged for the user; not an ADR (no architectural decision changes as a result).

---

## Decision 1 — Scheduler for FR-MON-1 (recurring monitoring)

**Decision**: `APScheduler` (`BackgroundScheduler`, in-process), started from a FastAPI `lifespan` context in `backend/app/main.py`, driving one interval/cron trigger that calls the `source_monitor` workflow's entrypoint. Concurrency safety (FR-MON-6: never fetch a source from two overlapping runs) is enforced with a Postgres advisory lock (`pg_try_advisory_lock`) keyed per `source_id`, not by trusting the in-process scheduler alone — this makes FR-MON-6 hold even if the service is ever run as more than one OS process.

**Rationale**: Nothing in the current stack provides recurring execution — FastAPI/Starlette/Uvicorn have no built-in scheduler, and the deployment target (`docker-compose.podman.yml`, single `backend` service, `network_mode: host`, Oracle VM) is explicitly a single-process, single-container deployment. APScheduler is a pure Python library (no broker, no second process, no new container) that runs inside the existing Uvicorn process and starts/stops with the app's own lifecycle — the smallest possible addition that satisfies "recurring schedule, without requiring a user-initiated search" (FR-MON-1). The advisory-lock guard is free (Postgres/Neon already the system of record) and makes FR-MON-6 correct even under `uvicorn --workers > 1` or an accidental second container, which an in-process-only lock would not.

**Alternatives considered**:
- **Celery + Redis/RabbitMQ beat scheduler** — rejected: introduces a broker, a second long-running worker process, and a new piece of deployed infrastructure on a single-VM Podman host that currently runs exactly one container. This is the "heavier option" the user asked to avoid unless justified, and nothing here needs its throughput/distribution guarantees at this scale (a handful of registry sources, one monitoring cycle at a time).
- **OS-level cron + a CLI entrypoint (`python -m app.workflows.source_monitor`)** — rejected: works, but moves scheduling outside the application (a second thing to configure and monitor on the VM, invisible to the app's own structured logging/metrics from NFR-HARDEN-4), and loses the in-process advisory-lock guard's natural home (the CLI invocation would need to open its own DB connection to take the lock anyway, so APScheduler-in-process costs nothing extra over this option while keeping everything inside one deployable unit).
- **LangGraph's own scheduling** — not a thing; LangGraph provides workflow graphs, not cron triggers. Irrelevant to this decision.

**What would force a heavier option later**: the moment this service needs to run as more than one instance behind a load balancer for availability/throughput (horizontal scaling), APScheduler's in-process timer becomes multiple independent timers firing in each replica. The advisory lock already prevents double-processing of a single source in that scenario, but *triggering* would become wasteful (every replica wakes up and finds nothing to do) rather than wrong. At that point, moving the trigger (not the workflow logic) to Celery-beat, a Kubernetes CronJob hitting an internal endpoint, or a managed scheduler (e.g. a cloud provider's cron-to-HTTP trigger) would be the right call — the `source_monitor` workflow itself does not need to change, only what calls it on a schedule.

---

## Decision 2 — `source_monitor` as a deterministic workflow, not an agent

**Decision**: `backend/app/workflows/source_monitor/graph.py`, a LangGraph `StateGraph` following the exact pattern already used by `backend/app/workflows/ingestion/graph.py` (node-per-stage, `_run_stage` wrapper that logs-and-short-circuits on failure rather than raising, no LLM call anywhere in the graph). It is a new **workflow module**, not a new agent, and is never added to `specs/charter.md`'s five-agent list.

Pipeline (per active, non-locked source): `select_sources_due -> fetch_listing (reuses `app/sources/connectors/official_fetch.py::fetch_and_extract_listing`, unchanged) -> diff_against_stored (new: compares extracted candidates to `scholarships`/`scholarship_sources` rows for that source) -> for each new/changed candidate: run_ingestion (reuses `app/workflows/ingestion/graph.py::run_ingestion` unchanged) -> lifecycle_transition (new: `services/lifecycle.py`, deterministic, drives `scholarships.lifecycle_status`) -> rematch_affected_owners (reuses `services/hard_constraints.py` + `services/matching.py::match_scholarship` unchanged) -> generate_alerts (new: `services/alerts.py`) -> record_monitoring_run_outcome`.

**Rationale**: FR-MON-5 is explicit ("MUST NOT be implemented as a new autonomous agent") and the constitution treats adding a sixth agent as an ADR-triggering, architecturally significant act. `source_monitor` only calls deterministic services and the two existing deterministic entrypoints above — it reasons about nothing with an LLM, so there is no agentic decision-making to justify an agent in the first place, and every guardrail from Principle II (hard constraints are computed by the deterministic service, never by an LLM) is inherited for free because the re-match step is the *same code path* `POST /scholarships/{id}/match` already uses, not a reimplementation.

**Alternatives considered**: a bespoke polling loop with plain Python functions (no LangGraph) — rejected only because it would duplicate `ingestion/graph.py`'s already-proven failure-logging/retry pattern (`_run_stage`) instead of reusing it; LangGraph here is not new technology, it is the same tool already used for `ingestion` and `url_match`, applied to a new graph.

---

## Decision 3 — Reuse `source_fetch_log` / `source_registry`; additive schema only

**Decision**: No existing column, table, or enum from 001 is altered or dropped. New Alembic migration(s) only add:

- `source_registry.listing_page_url: str | None` — FR-FETCH-1/2 needs a per-source configured listing page distinct from `domain`/homepage; this column does not exist today (`source_registry` currently has `domain` only). Nullable, so every existing row falls back to the homepage-fallback path (FR-MON-7) with zero backfill required.
- `source_registry.freshness_window_days: int | None` — FR-LIFECYCLE-2 needs a per-source freshness window ("Assumptions": these are operational config, not fixed spec values); nullable with a service-level default so existing rows are unaffected.
- `source_fetch_log.fetched_url: str | None` and `source_fetch_log.used_homepage_fallback: bool` (`server_default=false`) — FR-FETCH-3 requires every fetch outcome to record which URL was actually fetched, auditable per FR-MON-7's fallback-recording requirement. `official_fetch.py` already knows the URL it was given; this just persists it.
- `source_fetch_log.monitoring_run_id: uuid | None` (FK to the new `monitoring_runs.id`, nullable) — lets a fetch log row be attributed to a scheduled run vs. on-demand discovery, without changing the meaning of any existing row (all 001-era rows have `NULL` here, which simply means "not part of a monitoring run" — true for all of them).
- New table `monitoring_runs` (Key Entity "Monitoring Run") — `id, started_at, ended_at, trigger enum(scheduled, manual), sources_processed, sources_failed, changes_detected, status enum(running, completed, failed)`.
- New table `alerts` (Key Entity "Alert") — see Decision 5.
- New table `alert_preferences` — one row per user, `email_enabled bool default true`, `in_app_enabled bool default true` (FR-ALERT-5).
- New table `candidate_source_validations` (Key Entity "Candidate Source Validation Result") — see Decision 6 territory, FR-CANDVAL-1.
- No change to `lifecycle_status`'s enum values — see Decision 4, it already has all nine.

**Rationale**: the spec's own Assumptions section says this feature "does not redefine [the registry/ingestion/matching/scholarship model], and no change to `specs/001-scholarship-mvp/` is made or required." Every table above either already exists and only grows a nullable column (zero backward-compatibility risk, zero backfill), or is wholly new and owned by this feature. `source_fetch_log` and `source_registry` remain the single source of truth for health/coverage exactly as `services/coverage.py` already reads them — that module needs no change at all for FR-COVAN-1..3 beyond also counting `monitoring_runs`/`candidate_source_validations` where the spec asks for it, which is additive logic in the same file, not a redesign.

---

## Decision 4 — Lifecycle state as a single enum column

**Finding, not a new decision**: this is **already true today**. `backend/app/models/scholarship.py` defines `LifecycleStatus` (`StrEnum`) with exactly the nine spec values (`newly_discovered, verified, unverified, updated, expired, closed, reopened, stale, source_unavailable`) and `Scholarship.lifecycle_status` is a single non-nullable `Enum` column with that type — two states are already impossible by representation (a row has exactly one enum value; there is no second flag that could disagree with it). No schema change is needed for FR-LIFECYCLE-1.

What 002 adds is **behavior**, not schema: `services/lifecycle.py` (new, deterministic, pure — same style as `services/verification.py`, which already computes a subset of these transitions during ingestion) implementing the transition rules FR-LIFECYCLE-2..5 require (`-> stale` on freshness-window elapse, `-> source_unavailable` on exhausted retries distinct from `closed`/`expired`, `closed -> reopened` never `-> newly_discovered`). `services/verification.py::compute_lifecycle_status` already exists and partially overlaps (it's called from `ingestion/graph.py`'s `verify` node) — Decision 4's implementation work is extending that existing deterministic function/module with the monitoring-specific transitions, not creating a parallel lifecycle concept.

---

## Decision 5 — Alerts: in-app + email now, channel-abstracted

**Decision**: `services/alerts.py` defines a `NotificationChannel` Protocol (`send(alert: Alert) -> bool`) — the same "Protocol + concrete implementations behind one accessor" shape already used for storage (`app/data/files/storage.py`'s `StorageBackend` Protocol / `get_storage()`). Two concrete channels ship now:
- `InAppChannel` — writes/updates the `alerts` row's `delivered_in_app_at`; "delivery" for in-app is simply the row existing and being queryable via a new `GET /alerts` endpoint.
- `EmailChannel` — stdlib `smtplib` + `email.mime.text.MIMEText`, configured via new settings (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `ALERTS_FROM_EMAIL`) added to `core/config.py` exactly like the existing optional-external-service settings (`gemini_api_key`, `qdrant_url`) — optional/unset-safe so CI continues to need only `DATABASE_URL`/`JWT_SECRET_KEY`.

Adding a future channel (push/SMS/webhook, explicitly out of scope per spec Assumptions) means writing one more class that satisfies the Protocol and adding it to the dispatch list in `services/alerts.py` — no change to `source_monitor`, to the `alerts` table, or to how an alert is generated (OD-8).

**Rationale**: `smtplib` is in the Python standard library — zero new dependency for email. In-app delivery needs no delivery mechanism beyond a DB row and an existing-pattern GET endpoint. The Protocol-based dispatch is not new architecture for this codebase; it is the same seam `StorageBackend` already proved out for exactly the same reason (swap/add an implementation without touching callers).

**Alternatives considered**: a third-party transactional-email API (SendGrid/Postmark/SES SDK) — rejected for now: adds a new dependency and an external account/credential to manage for a feature whose spec only requires "at minimum, in-app and email" with no deliverability SLA; `smtplib` against any SMTP relay (including a free-tier one, e.g. the same provider hosting the Oracle VM, or Gmail SMTP for dev) satisfies the requirement with zero new dependency. If deliverability/analytics become a real requirement later, swapping `EmailChannel`'s internals for an API-based implementation is exactly the kind of change the Protocol boundary exists to absorb without redesign — consistent with OD-8.

---

## Decision 6 — Candidate-source validation (FR-CANDVAL) checks

**Decision**: `services/candidate_validation.py` (new, deterministic, no LLM) runs three checks against a `candidate_sources` row and persists the result as a new `candidate_source_validations` row (not overwritten in place, so FR-CANDVAL/Edge-Cases' "recency is shown, not stale-as-current" is satisfied by querying the latest row rather than losing history):
- **Reachability** — reuses `app/tools/web_fetch.py::fetch_url` (already used by `official_fetch.py`) against the candidate's `url`; pass/fail + HTTP status.
- **Extractability** — reuses `app/tools/extract_listing.py::extract_listing_from_page` (already used by `official_fetch.py::fetch_and_extract_listing`) against the fetched content; pass/fail on `accepted_count > 0`.
- **Official-source signals** — reuses the domain-pattern/keyword heuristics already written for `app/tools/detect_candidate_links.py` (gov/edu TLD patterns, institutional keyword matches) applied to the candidate's own domain/content, extended with a couple of additional heuristic checks (stated-affiliation text match) as pure functions in the same module — advisory only, per spec Assumptions ("heuristic assistive signals ... not a substitute for human judgment").

This never writes to `source_registry` and never flips `candidate_sources.status` — `source_repo.approve_candidate_source`/`reject_candidate_source` (existing, unchanged) remain the only human-gated promotion path (FR-CANDVAL-2/3/4, Principle IV, unchanged from 001).

**Rationale**: every primitive these three checks need (`fetch_url`, `extract_listing_from_page`, the candidate-link heuristics) already exists in `app/tools/`; the automation is a thin, deterministic composition of existing tools plus a new persistence row for the result, not new technology.

---

## Decision 7 — NFR-HARDEN-1..8 for the Podman/Oracle-VM target

| NFR | Approach | New tool? | Justification |
|---|---|---|---|
| **HARDEN-1** HTTPS | Add a `caddy` reverse-proxy sidecar service to `docker-compose.podman.yml`, terminating TLS (automatic Let's-Encrypt) in front of the existing Uvicorn `backend` service; HTTP requests get redirected by Caddy. | **Yes — Caddy**, justified below. | Nothing in the current stack terminates TLS. Uvicorn *can* take `--ssl-keyfile`/`--ssl-certfile` directly (zero new tool), but that still needs a certificate-issuance/renewal mechanism (e.g. certbot) bolted on separately — two things to add either way. A single reverse-proxy container that does both TLS termination and automatic renewal is the smaller total addition, and it does not touch `backend/app` at all (pure infra/compose change). |
| **HARDEN-2** Rate limiting | New Starlette middleware (`app/core/rate_limit.py`): in-memory token-bucket keyed by client IP, registered in `main.py`. | **No** — stdlib/Starlette only. | The deployment is a single container (no horizontal scaling today), so an in-memory limiter is correct and introduces no new dependency or external state (e.g. Redis). Revisit only if the service is ever scaled to multiple replicas (shared-state limiter would then need Redis — the heavier option). |
| **HARDEN-3** Retry/backoff | Reuse `app/sources/retry_policy.py::run_with_retry` exactly as `official_fetch.py` already does. | No. | Already exists and already satisfies this for every fetch path; monitoring calls the same connector. |
| **HARDEN-4** Structured logs/metrics | Logs: stdlib `logging` + a small JSON `Formatter` (no new dependency). Metrics: **`prometheus-client`**, exposing `GET /metrics` (new, unauthenticated-by-network-policy endpoint, consistent with Prometheus convention) incrementing counters/histograms from `source_monitor` and the rate-limit middleware. | **Yes — `prometheus-client`**, justified below. | Nothing in the stack emits metrics today; hand-rolling a metrics format/registry would reinvent what `prometheus-client` already does correctly (text-exposition format, histogram buckets) for one small, dependency-free library. Structured logging needs no new dependency — `logging.Formatter` subclassing is enough. |
| **HARDEN-5** Backups | Document (quickstart.md) that Neon (the existing managed Postgres) already provides automatic point-in-time-recovery/branch-based backup/restore — no code or new tool needed for the *database*. | No. | Neon's PITR is a platform feature of the storage already chosen in 001; building a custom pg_dump cron job would duplicate what the managed service already guarantees. |
| **HARDEN-6** Persistent document storage across redeploy | Mount a Podman named volume (or VM bind-mount) at the container path matching `STORAGE_ROOT`, declared in `docker-compose.podman.yml`, so `LocalFileStorage`'s files live outside the container's writable layer. | No. | `app/data/files/storage.py` already documents itself as swappable behind the `StorageBackend` Protocol "for an S3-compatible backend later" — for a single-VM deployment, a persistent volume is the smallest change that makes files survive a redeploy; no code change at all, only compose config. |
| **HARDEN-7** Fetch-history retention | New `services/retention.py`: deletes `source_fetch_log` rows older than a configured `FETCH_LOG_RETENTION_DAYS` **except** the most recent `HEALTH_WINDOW_N` rows per source (the same N used for FR-HEALTH-4), run on the same APScheduler instance as a low-frequency job. Policy + its rationale documented in `quickstart.md`. | No. | Reuses the scheduler already added for Decision 1; the "keep last-N regardless of age" clause is exactly what the spec's Edge Case requires (never prune the rows a current health/coverage-gap claim depends on to explain itself). |
| **HARDEN-8** Security hardening | (a) Dependency scanning: add a dev-only CI step (`pip-audit`, new **dev** dependency group entry) to `.github/workflows/ci.yml`. (b) Secrets: already `.env` + `pydantic-settings` (unchanged) — only new env vars are added to `.env.example`. (c) Sanitize externally-sourced content: extend the existing BeautifulSoup-based extraction path (already tag-stripping before `extract_listing_from_page`, per `official_fetch.py`'s own comments) with an explicit strip of `<script>`/`<style>`/`on*` attributes before any raw/evidence HTML is persisted or displayed — reuses `beautifulsoup4`, already a dependency. | **Yes — `pip-audit`**, dev-only. | Nothing today scans dependencies for known CVEs; `pip-audit` is the lightest standard tool for this and only runs in CI, never shipped. Sanitization and secrets management need zero new tools — both reuse what's already there. |

**Summary of genuinely new tools proposed** (everything else above reuses existing stack): **Caddy** (TLS termination/reverse proxy, infra-only), **APScheduler** (in-process recurring trigger), **`prometheus-client`** (metrics exposition), **`pip-audit`** (dev/CI-only dependency scanning). Each is justified above against a specific requirement the current stack cannot meet (no scheduler, no TLS termination, no metrics format, no dependency scanner exist today), and none of them requires a new deployed service/process beyond the one `backend` container plus the one `caddy` sidecar already needed for HARDEN-1.
