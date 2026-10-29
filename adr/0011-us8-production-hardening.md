# ADR-0011: US8 Production Hardening — Proxy-Trusted Rate Limiting, Run-Floored Retention, Contextvar JSON Logging, Loopback Backend Behind Caddy

- **Status:** Accepted
- **Date:** 2026-10-13
- **Feature:** 002-source-monitoring-hardening (US8 — NFR-HARDEN-1..8, tasks T216–T237)
- **Context:** US8 hardens a deployment that is a single Podman backend container (`network_mode: host`) on an Oracle VM, talking to a Neon *pooled* (transaction-mode) Postgres endpoint, with an in-process APScheduler. The Phase A review found four design points the spec and task list left open, plus several pieces missing from the task list (a request-ID source, a pinned `STORAGE_ROOT`, a loopback bind).

## Decision

1. **Rate limiting trusts the proxy through uvicorn, not through app code.**
   - T225's in-memory per-IP token bucket (capacity `RATE_LIMIT_PER_MINUTE`, refill limit/60 per second, injectable monotonic clock, bounded and purged bucket table) is a pure-ASGI middleware that returns `429` with a JSON body and `Retry-After`.
   - Behind Caddy, `request.client.host` is `127.0.0.1` for every caller, which would collapse the limiter into one global bucket. The backend therefore runs `uvicorn --proxy-headers --forwarded-allow-ips=127.0.0.1` (compose `command:` override), so the real client IP is already in `request.client` when the middleware runs. No hand-rolled `X-Forwarded-For` parsing in the app.
   - Only `/health` is exempt. The limit is per process (one worker today); with N workers the effective limit is N× and must be documented.
   - Postgres- or Redis-backed limiters were rejected: a Neon round trip on every request is the wrong cost and there is no Redis.

2. **Retention floors in runs, plus each source's most recent row.**
   - The retention job (T228) deletes only rows older than `FETCH_LOG_RETENTION_DAYS` that are not protected. Protected: (a) every row of each source's most recent `HEALTH_WINDOW_N` monitoring runs, ranked by the **same helper `_failure_streaks` uses** (extracted so health and retention cannot drift), and (b) each source's single most recent row of any kind, regardless of age.
   - (b) closes a gap in T228 as written: rows with a NULL `monitoring_run_id` were "prunable by age alone", so a source flagged `failing` by discovery's first-failure rule, whose only evidence is one old NULL-run row, would lose it and read as "never checked" in coverage (whose fallback is the latest raw row).
   - **Amendment (US8 B2, Q1): third floor (c)** — each source's most recent `OK` row with `items_found`, regardless of age. `official_fetch._last_successful_extraction` (extraction cache) and the monitor graph's `_previous_items_found` (shrink-guard baseline) read exactly that row; pruning it would cause a silent extraction-cache miss and a lost baseline (absence then never closes). Fails safe, but is an invisible behaviour change for long-stale sources, so it is protected.
   - Retention is scheduled daily (03:00 UTC) and coupled to `MONITORING_INTERVAL_MINUTES`: monitoring disabled means no scheduler and no retention (no extra setting). A manual `python -m app.services.retention [--dry-run] [--source-id ...]` entry point exists for the first production run.
   - Deletion is batched with one short transaction per batch, no session state and no lock, so it is safe on a transaction-mode pooler. The function takes a `source_ids` scope: tests run against the shared Neon database and must never prune real rows; production passes `None`. A `dry_run` count exists for the first production run, which deletes the whole backlog past the cutoff.

3. **JSON logging with contextvars; request IDs from a new middleware.**
   - A stdlib `logging.Formatter` emits single-line JSON (`timestamp`, `level`, `logger`, `message`, plus any `extra` fields). `request_id`, `run_id` and `source_id` live in a `ContextVar`, so the existing `logger.info("... %s", id)` calls gain those fields without being rewritten. No new dependency.
   - **Addition to the task list:** a request-ID middleware generates (or accepts a well-formed inbound) `X-Request-ID`, binds it, echoes it on the response and writes one access line (method, path, status, duration; never headers, bodies or query strings). T217 expects `request_id` but no task produced one.
   - `configure_logging()` runs in the app lifespan (after uvicorn has configured its own loggers, so ours wins) and routes uvicorn's loggers through the JSON handler. Its handler resolves `sys.stderr` at emit time so test capture cannot leave it holding a closed stream.

4. **The backend binds loopback; Caddy is the only public listener.**
   - With `network_mode: host` the backend's default `0.0.0.0:8000` stays reachable over plain HTTP even with Caddy in front, which defeats NFR-HARDEN-1. The compose override binds uvicorn to `127.0.0.1`, and T220's static test asserts it.
   - `STORAGE_ROOT=/data/storage` is set explicitly in the compose `environment:` (overriding `.env`) so the named volume mount and the setting cannot diverge (NFR-HARDEN-6).

## Accepted trade-offs (decisions Q1–Q6)

- **Q1 — extra retention floor:** adopted as decision 2(b); one extra clause in T219 and T228.
- **Q2 — no domain: `tls internal`.** Caddy serves a self-signed certificate; browsers warn. Acceptable for a personal / demo deployment. Production needs a real domain and Caddy's automatic HTTPS; T237 states this plainly.
- **Q3 — VM firewall:** the Oracle security list and OS firewall (80/443 open, 8000 closed) are an operator step in the T237 runbook, applied by the owner, not by this work.
- **Q4 — SSRF redirect limitation stays out of scope** (ADR-0010 unchanged). No redirect-aware fetch function is added; T237 records it as an accepted residual risk.
  - Superseded by ADR-0016 on 2026-10-29.
- **Q5 — `/metrics` has no role gate.** Any registered user can read it through the existing bearer auth; signup is open. Content is low-cardinality operational counts (no per-source labels). No Prometheus is deployed and tokens expire after 60 minutes, so access is manual `curl` with a fresh login; no scraper and no new auth tier.
- **Amendment (US8 B4, Q5): `/metrics` is VM-internal only.** The `Caddyfile` answers `/metrics` with `404` on the public listener, so Q5's "any registered user can read it through the existing bearer auth" now holds only for someone who can reach the loopback backend (`curl http://127.0.0.1:8000/metrics` from the VM with a fresh login token). The app-level bearer requirement (O-4) is unchanged and remains the second layer. This is a net security improvement: open signup no longer exposes operational counters to the internet. The no-role-gate decision itself stands. Also in B4: the site address defaults to the VM's public IP with `tls internal` (Q2 unchanged), `caddy:2-alpine` is pinned to the major version, and the firewall steps are written for Oracle Linux (firewalld + SELinux) in `docs/operations-hardening.md`.
- **Q6 — one global rate limit.** No stricter bucket on `/auth/login`, as the spec states.

## Consequences

### Positive

- Per-client limiting works behind the proxy with vetted forwarded-header handling.
- Health display and retention share one definition of "recent run"; a source's explanatory history cannot be pruned away.
- Every log line from a request or a monitoring run is attributable, with no call-site churn.
- No public plain-HTTP path to the backend.

### Negative

- Rate-limit state is per process and lost on restart.
- `tls internal` is not publicly trustworthy; users must accept a certificate warning.
- Open registration plus an unscoped `/metrics` means any account holder can read operational counters.
- The retention job's first run is a large one-time delete (mitigated by batching and `dry_run`).
- Context fields are only as good as where they are bound: code that logs from a thread started outside the request/run context will lack them.

## Alternatives Considered

- **Parse `X-Forwarded-For` in the middleware.** Rejected: easy to get wrong (spoofing, hop counting); uvicorn already does it correctly for a configured trusted peer.
- **Retention floor in rows (`HEALTH_WINDOW_N` rows).** Rejected in ADR-0008: one check writes 2–3 rows.
- **`structlog` / `python-json-logger`.** Rejected: a new dependency for a ~40-line formatter.
- **Leave the backend on `0.0.0.0` and rely on the firewall alone.** Rejected: one missed firewall rule re-exposes plain HTTP; the bind is free defense in depth.

## References

- Spec: `specs/002-source-monitoring-hardening/spec.md` (US8, NFR-HARDEN-1..8, SC-007..010)
- Tasks: T216–T237 (+ request-ID middleware, retention floor clause, loopback bind, pinned `STORAGE_ROOT`)
- Related ADRs: ADR-0008 (health derivation, runs-not-rows), ADR-0010 (SSRF limits), ADR-0006 (`_prior:*` rows are not pruned by this work)
