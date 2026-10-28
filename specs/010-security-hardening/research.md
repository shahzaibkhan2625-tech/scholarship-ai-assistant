# Research: Security Hardening of the Live Server (010)

**Date**: 2026-10-29 | **Spec**: [spec.md](spec.md) | **Code base**: `main` at `de39b9a` + spec commit

Every answer cites a file:line in this repository (paths relative to `backend/` unless shown) or a primary document (URL). "Observed" means run against the installed libraries in `backend/.venv` on 2026-10-29. Nothing here was assumed from memory.

**Result of the deviation check (FR-PROC-2): no new deviation from the Blueprint or PRD beyond D-1..D-4.** Four implementation defects found by this research are listed in section F; none contradicts a Blueprint/PRD statement, so they need no owner decision, but they do change the work (they are in scope of FR-MAIL, FR-UPLOAD, FR-SSRF, FR-DBROLE).

---

## R1 Neon least-privilege role

**Decision**: Create the application role with **SQL** (`CREATE ROLE … LOGIN PASSWORD … NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS`) run by the migration/owner role, not in the Neon console/CLI/API. Grant it `CONNECT`, `USAGE` on schema `public`, DML on all existing tables, `USAGE, SELECT, UPDATE` on all existing sequences, and `ALTER DEFAULT PRIVILEGES FOR ROLE <migration role> IN SCHEMA public` for tables and sequences created later. The exact script is `contracts/db-role.sql`.

**Evidence**:
- Roles created in the Console, CLI or API are "granted membership in the `neon_superuser` role", which carries CREATEDB, CREATEROLE, BYPASSRLS and REPLICATION; roles created with SQL "are only granted the basic public schema privileges" and are not members of `neon_superuser` — https://neon.com/docs/manage/roles . So the console would give the app exactly the rights we are removing; SQL does not.
- `ALTER DEFAULT PRIVILEGES FOR ROLE target_role` affects only objects **created by** that role, only objects created afterwards, and "at object creation time, only the default privileges of the creating role apply" — https://www.postgresql.org/docs/current/sql-alterdefaultprivileges.html . The migration role is the one that runs Alembic, so it must be named in `FOR ROLE`. Existing tables need a one-off `GRANT … ON ALL TABLES IN SCHEMA` (the same page says defaults do not touch existing objects).
- Postgres 15 removed `CREATE` on schema `public` from `PUBLIC` only for **new** databases; "upgrading a cluster or restoring a database dump will preserve public's existing permissions" — https://www.postgresql.org/docs/release/15.0/ . The live database's age is unknown, so the script runs `REVOKE CREATE ON SCHEMA public FROM PUBLIC` (owner-run, harmless where already revoked) and the post-deploy check actually attempts `CREATE TABLE` as the app role (SC-003).
- Pooled vs direct: Neon says "use a pooled connection by default" and a **direct** connection for "schema migrations"; the pooled string has `-pooler` in the host — https://neon.com/docs/connect/choose-connection . So: app `DATABASE_URL` = restricted role on the **pooled** host (as today's pooled string, runbook §13 row "Database"); migrations = owner role on the **direct** host (matches the existing runbook note at `docs/operations-hardening.md:54-56`).
- The migration role today is `neondb_owner` (Blueprint §19 implementation status, `specs/scholarship-ai-assistant-master-blueprint-v1.md:648`). It is a console-created role, so it keeps `neon_superuser`; the new app role is not.

**Code consequences**: `app/core/config.py:16-17` has one `DATABASE_URL`. Add an optional `MIGRATION_DATABASE_URL`; `migrations/env.py:30-32` (`config.set_main_option("sqlalchemy.url", settings.database_url)`) uses it when set. The owner URL is passed **only** to the one-off migration command (`podman run --rm -e MIGRATION_DATABASE_URL=…`, the pattern at `docs/operations-hardening.md:54-56`), never written to the VM `.env`, so FR-DBROLE-2 holds.

**Alternatives**: (a) console-created role + `REVOKE` of `neon_superuser`: Neon documents no supported revoke path, and the page says the Console grants it; rejected. (b) Postgres row-level security per table: wrong tool, the app has no per-role policy model; rejected. (c) One shared role for app and migrations: that is today's exposure.

---

## R2 Redirect-aware, rebinding-safe fetch with httpx

**Decision**: Replace the single `httpx.get(..., follow_redirects=True)` in `app/tools/web_fetch.py:27-32` with a loop that (1) resolves the host, (2) refuses if **any** address is non-public, (3) connects to **one validated address** by putting that IP in the request URL, keeping the original name in the `Host` header and in the `sni_hostname` request extension, (4) sets `follow_redirects=False` and handles `301/302/303/307/308` itself, repeating (1)–(3) per hop up to 5 hops, (5) uses `trust_env=False` so proxy variables cannot redirect the pinned connection. The guard function moves unchanged from `app/services/candidate_validation.py:68-109` (`_resolve_host`, `_non_public`, `unsafe_url_reason`) to a shared module so `app/tools` can use it without importing from `services`; `candidate_validation` re-exports the names (its tests import them).

**Evidence**:
- `sni_hostname` "sets the server hostname that HTTPX uses to check the SSL certificate … when the URL contains an IP address you pass the IP in the URL, set the `Host` header to the domain, and set `sni_hostname` to the same domain" — https://www.python-httpx.org/advanced/extensions/ . Observed in the installed `httpcore`: `_connect` reads `request.extensions.get("sni_hostname")` (`httpcore/_sync/connection.py`, 3 references), so TLS hostname verification stays on, against the real name, while the TCP connection goes to the pinned IP. This closes the DNS-rebinding gap that ADR-0010 decision 2(b) accepted (the guard resolves, then httpx resolves again).
- Manual redirects: httpx's own redirect builder is `Client._build_redirect_request` (observed); with `follow_redirects=False` the 3xx response and its `Location` header are returned to us, which is what lets us check each hop *before* any request is sent to it. This is what ADR-0010 "Alternatives" called "modify `fetch_url` to expose redirects — deferred"; D-1 un-defers it.
- A custom transport is also possible (https://www.python-httpx.org/advanced/transports/ shows subclassing `BaseTransport`), but that page does not cover IP pinning; the URL-rewrite + `sni_hostname` route is the one the extensions page documents.
- `unsafe_url_reason` already handles scheme, `localhost`, IPv4-mapped IPv6 and multi-address resolution (`candidate_validation.py:73-109`: `ip.is_global` after unmapping). It does **not** handle numeric encodings such as `http://2130706433/`, `http://0x7f.1/` or `http://017700000001/`; `urlparse().hostname` returns those as strings that `ipaddress.ip_address` rejects, and `getaddrinfo` (POSIX `inet_aton` rules) accepts them. The new guard must therefore resolve first and judge the **resolved** addresses, which already happens for non-literals (they fall to the `resolve()` branch). Observed gap to close with a test: literal-but-unparsed hosts are resolved, not skipped.
- Unresolvable host: the guard returns `None` (`candidate_validation.py:103-104`) and the fetch reports "unreachable". Kept (spec edge: not an SSRF target).

**Where the guard now applies (all outbound fetches in the app)**: `app/tools/web_fetch.py:fetch_url` is the single entry used by URL match (`workflows/url_match/graph.py:62`), the monitor (`workflows/source_monitor/graph.py:100,547`), the official-fetch connector (`sources/connectors/official_fetch.py:154,318`), candidate validation (`services/candidate_validation.py:185`) and `workflows/source_validate/graph.py:26`. The only other outbound call is `sources/connectors/api_connector.py:64` (`httpx.get(f"https://{source.domain}")`, redirects off by default); it is moved onto the same helper with 0 redirects so FR-SSRF-1 holds for registry API sources too.

**Result shape**: `FetchResult` (`web_fetch.py:12-20`) gains `blocked: bool = False`. A blocked or over-long-redirect fetch returns `success=False, blocked=True, error="blocked: …"`. URL match already maps a failed fetch to HTTP 422 `FetchFailure` (`api/url_match.py:36-37`), so the student gets a clear error without a route change; the refusal log line is written by the helper (user and entry point are added by the route/graph through the existing `log_context`).

**Alternatives**: (a) keep `follow_redirects=True` and check `response.history` afterwards: that is today's behaviour — the request has already been sent. (b) A third-party SSRF library: new dependency, not needed. (c) Egress firewall on the VM: a good second layer (record in the runbook as optional), not a substitute for the app check because the failure must be a clear error.

**Residual**: a TOCTOU between our `getaddrinfo` and `connect` is removed by pinning; DNS answers with a very short TTL are irrelevant because we connect to the address we judged. Response size is not capped by this feature (out of scope, noted in Risks).

---

## R3 Broad catches that can swallow a database error (FR-RECOV sites)

Method: `grep -rn "except Exception|except:" app --include=*.py` (22 sites), then read each for whether the `try` body uses the SQLAlchemy `Session`.

**Sites that continue after a possible DB error and do NOT roll back — must be fixed (FR-RECOV-1):**

| # | file:line | what the `try` does with `db` | audit row |
|---|---|---|---|
| 1 | `app/sources/connectors/official_fetch.py:308` (`except Exception: pass`) | `source_repo.get_active_sources` + `record_or_bump_candidate_source` (writes + commit) | P4-OF-4 |
| 2 | `app/agents/discovery/agent.py:285` | `_ingest_candidates` / `_run_web_listing_step` write through `db`; later `get_coverage_summary(db)` at ~`agent.py:337` | P4-DA-4 |
| 3 | `app/workflows/ingestion/graph.py:133` (`_run_stage`, non-retryable path) | the `store` stage commits through `state["db"]`; then `_log_failure(state["db"], …)` writes | same class as P4-OF-4 (found by this research) |
| 4 | `app/workflows/ingestion/graph.py:121-124` (retryable path: `run_with_retry` returns `outcome.last_error`; `app/sources/retry_policy.py:60` is the catch) | same `_log_failure` write follows a failed stage | same class |

**Sites that already roll back (kept, covered by the new test):** `workflows/source_monitor/graph.py:414` (rollback at 416), `:492` (494), `:513` (515), `:520` (522), `:578` (580); `services/alerts.py:335` (rollback at 337). `app/data/repositories/monitoring_repo.py:38` is the lock-release rollback, not a catch.

**Sites with no session in the `try` body (no change; listed so the test can allow-list them with a reason):** `services/alerts.py:138` (SMTP only), `:231` (`channel.send`: the email channel only mutates the in-memory `Alert`; `alert_repo.save` is outside the try), `services/candidate_validation.py:153,173` (fetch / LLM extraction only), `tools/classify.py:140` (LLM only), `tools/pdf_parse.py:32,47` (PyMuPDF), `workflows/ingestion/graph.py:648` (`ingest_html_page` → vector store, `rag/embed.py:13`), `core/request_id.py:61` (re-raises after a 500), `api/monitoring.py:31` and `scheduling/scheduler.py:38,49` and `main.py:47` (each inside its own `with SessionLocal()` block, which closes the session on exit).

**Decision**: add `db.rollback()` (guarded so a failing rollback cannot mask the original error) in sites 1–4 and add an AST test (`tests/security/test_broad_catch_rollback.py`) that fails when a new `except Exception` appears in `app/` without either a rollback in the handler or an entry in a reasoned allow-list. This is cheaper and more durable than eight hand-written fault-injection tests, and it is the "grep" check the audit used for P4-DA-4 ("Only monitoring code calls `rollback()`"). Behavioural fault-injection tests are still written for sites 1–3 (SC-007).

---

## R4 Upload limits and PDF detection

**How reads work today**:
- Document upload: `api/documents.py:46,57` — `def upload_document(file: UploadFile …)`, `file_bytes = file.file.read()` (whole file, no cap), then `get_storage().save(...)`; type check is a filename suffix in `workflows/doc_pipeline/graph.py:113-117` (`.pdf`), so a PDF named `x.bin` is rejected and a non-PDF named `x.pdf` is stored then fails at parse.
- CV import: `api/profile.py:82-87` — `async def import_cv`, `await file.read()`; `_extract_text_from_upload` (`:20-24`) decides by `content_type` ending `pdf` **or** a `.pdf` name, otherwise decodes the bytes as UTF-8 text (so a non-PDF is accepted as text today) and `PdfReader` errors become 500.
- Starlette buffers multipart: files spool in a `SpooledTemporaryFile` with `spool_max_size = 1024 * 1024` (observed, `starlette/formparsers.py:147,230`); `max_part_size` (1 MB) applies to non-file fields only (observed signature). So memory is bounded by Starlette, but **disk and time are not**: a 5 GB body is written to a temp file before our handler runs. A cap must therefore act on the body stream.

**Decision** (no new dependency):
1. **Edge cap in Caddy**: `request_body { max_size 11MB }` for the site (Caddy answers 413 when the read passes the limit — https://caddyserver.com/docs/caddyfile/directives/request_body ). 11 MB = 10 MB file + multipart overhead; all other API bodies are small JSON.
2. **App cap**: a small pure-ASGI middleware (same pattern as `core/rate_limit.py` and `core/request_id.py`) that rejects a request whose `Content-Length` exceeds the limit for the two upload paths with 413, and counts streamed bytes for chunked bodies. Defence in depth for local/Docker runs without Caddy.
3. **Handler cap**: a shared helper `read_pdf_upload(file) -> bytes` reads at most `10 MiB + 1` bytes (`file.file.read(limit + 1)`); more than 10 MiB → 413 "file too large"; exactly 10 MiB accepted (edge case in the spec).
4. **Content check**: the bytes must contain `%PDF-` in the first 1024 bytes. pypdf's own reader looks for the header at the start and tolerates leading junk (`pypdf/_reader.py:859-879`, header `%PDF-`), and the PDF convention is a header within the first 1024 bytes. Not a PDF → 415 "only PDF files are accepted". Empty → 400/422 "file is empty". PyMuPDF (`tools/pdf_parse.py:31-47`) remains the real validator and already raises typed `PdfParseError` for corrupt/encrypted input; CV import gets the same typed error → 422 instead of a 500.
5. `_identify_type_node` (`doc_pipeline/graph.py:113-117`) stops looking at the filename (the route has already validated content), so a PDF with another extension works.

**Alternatives**: (a) `python-magic`: new dependency plus a native libmagic on a slim image; a 5-byte header test is what "is it a PDF" means here, and the parser is the second check. (b) Only trust `Content-Length`: a chunked upload bypasses it. (c) Stream to disk and size the file afterwards: still writes the attacker's bytes.

---

## R5 CV import must not block other requests

**Finding**: `api/profile.py:82` is `async def` but its body calls blocking PDF parsing and the blocking LLM call `extract_profile_from_text` (`profile.py:89`). FastAPI: "an `async def` path operation runs directly on the event loop … if it calls blocking code the whole event loop stops"; "a path operation declared with plain `def` is run in an external threadpool"; "If you just don't know, use normal `def`" — https://fastapi.tiangolo.com/async/ . The compose file runs one uvicorn process (`docker-compose.podman.yml` `command:`), so one slow import stalls every user (audit P4-PR-1).

**Decision**: make `import_cv` a plain `def` (sync file read through `file.file.read`, like `upload_document`). No new library, no `run_in_threadpool` wrapper needed. The scheduler runs in its own thread already.

**Test (SC-005)**: with an `httpx.AsyncClient` over `ASGITransport`, start `POST /profile/import-cv` with a fake extractor that sleeps 2 s in a thread, then `GET /health` must return in under 1 s. With today's `async def` the test fails (the loop is blocked), which makes it a genuine red test.

**Limit to record**: the threadpool is finite (default 40 threads); many simultaneous imports can still exhaust it. That is the same limit every other sync route has and is covered by the per-IP rate limiter (`core/rate_limit.py`). Per-user LLM limits are 003.

---

## R6 Password hashing and email handling

**Today**: `app/core/security.py:11` `CryptContext(schemes=["bcrypt"])`; `hash_password` (`:14`) and `verify_password` (`:18`) pass the string straight through. `uv.lock` pins `bcrypt 4.0.1` and `pyproject.toml:9` requires `bcrypt<4.1`. Observed: `p.hash("a"*72+"X")` then `p.verify("a"*72+"Y", h)` → **True** — bytes beyond 72 are silently ignored (matches audit P4-AU-1). `schemas/auth.py:7-9` has `password: str` with no constraint; `EmailStr` is not lower-cased in the local part.

**Decision**:
- Validate in the **schema** so a bad password never reaches the hasher: `SignupRequest.password` → min 8 characters, `len(value.encode("utf-8")) <= 72`, clear messages, no composition rules. `LoginRequest.password` gets **no** length rule (FR-CRED-4: existing short or >72-byte accounts must still sign in; login of an over-long string behaves as today and is not an enumeration or 500 risk).
- Defence in depth: `hash_password` raises `ValueError` for >72 bytes (so a future caller cannot reintroduce silent truncation). Keep the `bcrypt<4.1` pin (passlib 1.7.4 reads `bcrypt.__about__`, which later bcrypt releases removed; bcrypt ≥5 also *raises* on >72 bytes, which is another reason to reject up front). No algorithm change.
- Email: today sign-up and login compare the exact string (`api/auth.py:19,41`; `models/user.py:15-16` `email` unique). Normalise with `strip().lower()` in both request schemas, store lower-case, look up with `func.lower(User.email) == normalised` so **existing mixed-case rows keep working** (FR-CRED-4), and add a **unique index on `lower(email)`** in the migration. The index build fails if two existing rows collide, which turns FR-CRED-5 into a hard stop; the runbook pre-check lists collisions first. A concurrent duplicate sign-up now raises `IntegrityError` → mapped to the same 409 (today it would be a 500).
- Secret strength (FR-CRED-3): a `model_validator` on `Settings` (`config.py:18`) rejecting `len < 32` or the `.env.example:9` placeholder `change-me-to-a-random-secret`. Because `Settings` is imported by Alembic and the CLI too, they also refuse a weak secret — intended. Consequence: CI sets `JWT_SECRET_KEY: dummy-ci-secret-not-real` (`.github/workflows/ci.yml:29`, 24 chars) and two tests set `"test-secret"` (`tests/core/test_config_002.py:25`, `tests/services/test_alerts.py:359`); they must change to ≥32 characters. The message must not print the value.

---

## R7 Gmail SMTP and how alerts are sent today

**Gmail facts**: host `smtp.gmail.com`; "use port 465 (for SSL) or port 587 (for TLS)" — https://developers.google.com/workspace/gmail/imap/imap-smtp . An App Password is a 16-character code; "App passwords can only be used with accounts that have 2-Step Verification turned on"; unavailable for accounts with only security keys, work/school accounts or Advanced Protection; changing the Google password revokes them — https://support.google.com/accounts/answer/185833 . Google shows the code in groups of four; spaces are cosmetic, so the config strips them.

**Chosen settings** (all existing variables, `config.py:38-42`, `.env.example` lines 19-24): `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587` (STARTTLS, the existing default), `SMTP_USERNAME=<owner gmail>`, `SMTP_PASSWORD=<app password>`, `ALERTS_FROM_EMAIL=<same gmail>` (Gmail rewrites other From addresses).

**How it sends today** (`app/services/alerts.py:86-143`): `EmailChannel.send` returns False and writes `alert.email_error` when `SMTP_HOST`, the recipient or `ALERTS_FROM_EMAIL` is missing (`:117-124`); otherwise `smtplib.SMTP(host, port, timeout)`, `smtp.starttls()`, optional `login`, `send_message` (`:133-137`); any exception is caught (`:138`) and stored in `email_error`. `dispatch_alert` (`:225-236`) calls each channel and persists the alert row, so the in-app alert always exists (FR-MAIL-2 already holds structurally). No log line is written for "skipped" or "failed" — only a DB column, which `AlertRead` exposes to the student (`schemas/alert.py:21`).

**Defects to fix (F-1, F-2)**:
- **F-1 (security):** `smtp.starttls()` with no context: observed `ssl._create_stdlib_context()` has `verify_mode = 0 (CERT_NONE)` and `check_hostname = False`, and `SMTP.starttls` uses it when `context is None`. The App Password would travel over a TLS session that does not verify the server certificate. Fix: pass `ssl.create_default_context()`.
- **F-2 (FR-MAIL-2 / information leak):** add `logger.warning` lines for "not configured" (once per run is enough) and for failures (exception type and Google's reply code, never the password), and stop exposing raw exception text through the student-facing `email_error` field: keep a short reason code there (`not_configured`, `send_failed`) and the detail in the log. This keeps the field's meaning and removes server detail from a student-visible response. (The field itself stays; no API removal.)

**Test**: a fake SMTP server on localhost (stdlib `smtpd` is gone in 3.12; use a small `socketserver` fake or monkeypatch `smtplib.SMTP` with a recording class — the existing tests already stub `smtplib`, `tests/services/test_alerts.py`). Live send is verified once by the owner in the post-deploy checklist.

---

## R8 Operator CLI pattern

`python -m app.services.retention` is the existing pattern (`app/services/retention.py`, tail): `argparse` parser built in `main(argv: Sequence[str] | None = None) -> int`, session opened with `with SessionLocal() as db:` inside `main`, result printed, `if __name__ == "__main__": raise SystemExit(main())`; invoked on the VM as `sudo podman exec scholarship-ai-assistant_backend_1 uv run python -m app.services.retention` (`docs/operations-hardening.md:190-191`). `app/services/source_registry_seed.py` follows the same shape.

**Decision**: `app/services/operator.py` with `main(argv)`: `grant --email X` and `revoke --email X`, exit code 0 on success/no-op, 1 on "no such user", 2 on usage error (argparse). Grant writes the audit record in the same transaction as the flag change (R9). The command is run **inside the backend container on the VM**, which holds only the restricted DB credentials; granting needs `UPDATE` on `users` and `INSERT` on `audit_log`, both inside the app role's rights, so no owner credential is needed. Email is normalised the same way as sign-up. Re-granting an operator prints "already an operator" and writes no second audit row (spec edge case).

---

## R9 Where each audited action happens today, and one-transaction audit writes

| Action | Where it happens | Commit point today | How the audit row joins the same transaction |
|---|---|---|---|
| sign-up | `api/auth.py:23-26` (`db.add(user)`, `commit`) | route | `db.add(user)`; `db.flush()` (gets the id); `audit_repo.record(db, user.id, "user.signup", f"user:{user.id}")`; one `commit` |
| source approval | `data/repositories/source_repo.py:216-248` | `db.commit()` at `:245` | `record(...)` before that commit; actor = `reviewed_by` |
| source rejection | `source_repo.py:251-268` | `db.commit()` at `:265` | same |
| manual monitoring run | `api/monitoring.py:64-79` → `monitoring_repo.create_run_if_none_running` (`:31`) → `create_run` (`:18`) commits | inside `create_run` | add an optional `actor_id` parameter; when given, `db.flush()` the run and `record(...)` before the commit. The scheduler passes none, so scheduled runs are not audited (spec lists manual only) |
| submission approval | `api/application.py:119-157` → `application_repo.create_submission_approval` (`:114-140`) | `db.commit()` at `:138` | `record(...)` before that commit; actor = `user_id` |
| operator grant | CLI (R8) | CLI | flag update + `record(...)`, one commit |

`audit_repo.record` only does `db.add(AuditLog(...))` and never commits, so "the action and its record succeed or fail together" (FR-AUDIT-2) follows from the existing single commit; a failed insert raises, the commit never happens and the route returns an error. A refused request (403/409/422) never reaches those lines, so nothing false is recorded.

Append-only (FR-AUDIT-3) is enforced by privileges, not by convention: the app role gets `INSERT, SELECT` on `audit_log` and `UPDATE, DELETE, TRUNCATE` are revoked (data-model.md). The tests suite runs as the app role in CI, so an accidental `UPDATE` would fail there.

**No read route** is added (spec: "if any read path exists"). The owner reads the table with owner credentials (runbook query). Test: the OpenAPI document has no path mentioning `audit`.

---

## R10 Deployment today and what changes

**Today** (`docs/operations-hardening.md` §2 steps 1-6, §13; `docs/vm-deployment-notes.md` "Verified deployment procedure"): rootful podman; `git pull` on `main`; `sudo /usr/local/bin/podman-compose -f docker-compose.podman.yml up -d --build`; migrations by `sudo podman exec scholarship-ai-assistant_backend_1 uv run alembic upgrade head`; `.env` on the VM holds `DATABASE_URL` (Neon pooled, owner), SMTP_* (unset), JWT secret; compose forces `STORAGE_ROOT`; Caddy is the only public listener, `/metrics` is answered 404 in `Caddyfile`; rollback kept by tagging the previous image (`:317`).

**Changes for two database URLs and the rest of 010** (full sequence in `quickstart.md` §Deploy):
1. Owner runs `contracts/db-role.sql` once on Neon (direct URL) → creates `scholarship_app`.
2. Build the new image **without** swapping the running container. Run migrations as a one-off container with the owner URL passed on the command line: `sudo podman run --rm --env-file .env -e MIGRATION_DATABASE_URL=<owner direct URL> <new image> uv run alembic upgrade head`. All 010 migrations are additive (new column with default, new table, new index), so the old code keeps running through the gap.
3. Edit the VM `.env`: `DATABASE_URL` → `scholarship_app` on the pooled host; set `JWT_SECRET_KEY` to ≥32 characters if it is not; set `SMTP_*`. Recreate the container (env is read at start, `operations-hardening.md:153-154`).
4. `podman exec … uv run python -m app.services.operator grant --email <owner>`.
5. Post-deploy checks (US9 scenario 2), written as a checklist plus a script, and recorded in §13 of the runbook.
6. Caddyfile gains `request_body { max_size 11MB }` (validated with `caddy validate` as in `operations-hardening.md:44`).
7. Rollback: previous image tag + previous `.env` (kept as `.env.pre-010`); the additive migrations need no downgrade for the old code to run; if `DATABASE_URL` must return to the owner role it is only a `.env` edit.

CI: `.github/workflows/ci.yml` and `deep-ci.yml` (both Postgres 16 service, `DATABASE_URL` = `postgres` superuser, migrations then tests) change to: create `scholarship_app` with the same SQL, run `alembic upgrade head` with `MIGRATION_DATABASE_URL` = `postgres`, run `pytest` with `DATABASE_URL` = `scholarship_app` (FR-DBROLE-4). Tests use rollback-wrapped DML only (`tests/conftest.py:200-225`; no `create_all`/DDL in `app/` or `tests/` — grep), so they should pass under the restricted role.

---

## F Other findings from the code read

- **F-1** SMTP STARTTLS does not verify the certificate (R7). Fix in FR-MAIL.
- **F-2** Student-visible `email_error` carries raw exception text (R7). Fix in FR-MAIL.
- **F-3** `candidate_validation` guard does not run on redirects (D-1, R2) — known.
- **F-4** `GET /sources/candidates` (`api/sources.py:42`) and `GET /monitoring/runs[/{id}]` stay open to every signed-in user, as the approved assumption says. `GET /monitoring/runs/{id}` returns `source_outcomes` built from `SourceFetchLogRead` (`api/monitoring.py:55-61`), i.e. **raw fetch-log rows by another route**. The approved assumption said "If the plan finds a route that does expose fetch-log rows, it follows FR-PROC-2." — see **Owner question Q-1** in `plan.md`. This is the one item that needs the owner's decision before tasks are written; it is not a Blueprint/PRD deviation (it is an ambiguity inside the approved spec).
- **F-5** Reserved-name list for FR-SSRF-6: IANA's special-use registry lists `alt, example, invalid, local, localhost, onion, test, home.arpa` (+ `*.arpa` service names) — https://www.iana.org/assignments/special-use-domain-names/special-use-domain-names.xhtml . It does **not** list `internal`, `corp`, `lan`, `home`, `intranet`, `private`, `localdomain`; these are conventional private suffixes (ICANN's name-collision page does not list them either), so they go in a documented constant beside the IANA names. Final backstop is the resolution check, which refuses anything resolving to a private address.
