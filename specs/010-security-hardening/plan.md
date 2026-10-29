# Implementation Plan: Security Hardening of the Live Server

**Branch**: `010-security-hardening` | **Date**: 2026-10-29 | **Spec**: [spec.md](spec.md) (approved, with the five assumptions of 2026-10-29)
**Input**: `/specs/010-security-hardening/spec.md`; research in [research.md](research.md)

## Summary

Nine stories close the gaps the audit found on the public server. The technical approach, all from research and all with the standard library or existing dependencies:

1. **Operator role** (US1): one boolean on `users`, read fresh on every request, a `require_operator` dependency on the gated routes, and a CLI (`python -m app.services.operator grant|revoke`) as the only way to change it.
2. **Safe fetch** (US2): one rewritten `fetch_url` that resolves, checks every address, **connects to the checked address** (IP in the URL, name kept in `Host` and `sni_hostname`), and follows redirects itself with the same check per hop (5 max). The guard moves out of `candidate_validation` unchanged; approval gets a public-host-name rule and a 409 for duplicates.
3. **Least privilege** (US3): a SQL-created `scholarship_app` role (script in `contracts/db-role.sql`), a second optional `MIGRATION_DATABASE_URL`, a start-up warning, and CI that migrates as owner and tests as the app role.
4. **Credentials** (US4): schema validators, case-insensitive email with a `lower(email)` unique index, and a refuse-to-start check on the signing secret.
5. **Uploads** (US5): a 10 MiB (10 485 760 bytes) cap at three layers (Caddy, a small ASGI middleware, a bounded read); document upload is PDF only by content; CV import keeps PDF-or-plain-text, with text decoded strictly as UTF-8 (no `errors="ignore"`); `import_cv` becomes a plain `def` so it runs in the thread pool.
6. **Audit** (US6): an `audit_log` table written in the same transaction as each action; append-only by database privilege; no read route.
7. **Recovery** (US7): rollback at the four unprotected broad catches plus an AST test that keeps new ones honest.
8. **Mail** (US8): Gmail over verified STARTTLS (fixes an unverified-TLS defect), log lines, and short reason codes instead of raw errors.
9. **Deploy** (US9): ordered runbook with owner-credential migration as a one-off container, post-deploy checklist, rollback.

**Migration: yes**, one additive Alembic revision (`users.is_operator`, unique index on `lower(email)`, `audit_log`). **New dependency: none.**

## Decision recorded: Q-1 = A (owner, 2026-10-29)

`GET /monitoring/runs/{run_id}` returns `source_outcomes` built from raw fetch-log rows (`app/api/monitoring.py:55-61`), so it becomes **operator-only** like `GET /sources/{id}/fetch-log`. `GET /monitoring/runs` (the list, which carries no fetch-log rows) stays open to every signed-in user. The operator-only set is therefore: approve, reject, manual run, run detail, source fetch log, candidate validations. This closes the gap found in research (F-4) without any difference from the Blueprint or PRD beyond D-1..D-4; D-2 below includes the run-detail route. `/sp.tasks` is no longer blocked by this item.

## Pre-flight evidence (Blueprint §23 rule 3)

The owner's read-only checks behind this feature: **2026-10-27** VM `.env`, ports and database role facts (the app connects as `neondb_owner`); **2026-10-28** Neon counts unchanged (0 users, 34 scholarships, 4 ACTIVE and 1 FAILING sources); **2026-10-29** VM containers `backend` and `caddy` up, VM at commit `5c1c021`. The deploy tasks re-check the relevant facts before changing anything (T435).

## Technical Context

**Language/Version**: Python 3.12 (existing)
**Primary Dependencies**: FastAPI 0.141, Starlette 1.6, SQLAlchemy 2.0, Alembic 1.19, httpx 0.28.1 (+ httpcore), passlib 1.7.4 + bcrypt 4.0.1 (pinned `<4.1`), pydantic 2 / pydantic-settings, PyMuPDF, pypdf, APScheduler 3, psycopg 3. Standard library: `ipaddress`, `socket`, `ssl`, `smtplib`, `argparse`. **No new dependency** (research R2, R4, R5: `python-magic`, an SSRF library and a body-limit package were each considered and rejected).
**Storage**: PostgreSQL on Neon (pooled endpoint for the app, direct endpoint for migrations); local-filesystem document storage unchanged.
**Testing**: pytest with the existing rollback-per-test fixtures (`tests/conftest.py:200-225`); `httpx.MockTransport` + injected resolver for the network; fake SMTP; AST tests for catches and for "no new route". CI runs the suite as the restricted role.
**Target Platform**: Linux container (podman) on the Oracle VM behind Caddy; one uvicorn process.
**Project Type**: Backend-only modular monolith.
**Performance Goals**: one extra indexed read per request is already paid (`get_current_user` loads the user); fetch adds one DNS lookup per hop (previously two, unchecked); upload cap rejects over-size before parsing. No throughput target changes.
**Constraints**: student features unchanged (FR-AUTHZ-6); every capability has an entry point and a test starting there (FR-PROC-1); no secrets in the repo or logs; owner credentials never in the app container.
**Scale/Scope**: 0 production users beyond the owner; open sign-up; ~22 broad catches; 6 gated routes; 3 upload/fetch entry points.

## Constitution Check (gate, before Phase 0)

| Principle / rule | Assessment | Result |
|---|---|---|
| I No fabrication | No LLM output path changes. Refusals say what was refused and why; nothing is inferred. | Pass |
| II Deterministic matching | Matching code untouched. | Pass |
| III No live submission without human approval | The submission-approval route only gains an audit write; `tests/api/test_no_live_submission.py` stays and still passes. | Pass |
| IV Registry-approved sources only | Strengthened: only an operator can change source authority; approval domain must be public; fetches cannot be steered to internal hosts. The existing AST test keeping promotion out of `candidate_validation` stays. | Pass |
| V Spec-driven; tests and evals gated in CI | Spec approved; this plan; tests per story (security is a named test category). No agent behaviour changes, so eval suites are unchanged and must still pass; CI gains the restricted-role run. | Pass |
| Five agents; boundaries | No agent added or redefined. | Pass |
| Harness discipline / external reach | The fetch tool now enforces the permission wall (no reaching internal systems). | Pass |
| Stack constraints | FastAPI / PostgreSQL (Neon) / Qdrant / Docker / GitHub Actions unchanged. A second database *role* is not a change of core component. | Pass |
| Workflow: ADRs on significant decisions | Yes: see "ADR suggestion". Not auto-created. | Pass (action pending) |
| PHRs | **Conflict:** the constitution requires a PHR per prompt; the owner's standing rule (CLAUDE.md) is PHR only on "PHR now". The owner's rule governs; a constitution amendment is scheduled after 010. | Noted (not changed here) |

**Gate result: pass, no violations; Complexity Tracking not needed.**

## Design by story

File paths are relative to `backend/`. "T:" lists the tests (all start at the entry point in the spec's map).

### US1 Operator-only governance (FR-AUTHZ-1..7)
- `app/models/user.py`: `is_operator`. `app/api/deps.py`: `require_operator(current_user = Depends(get_current_user))` → 403 `Operator access required`.
- Apply to `app/api/sources.py` approve (`:51`), reject (`:74`), validations (`:97`); `app/api/monitoring.py` `POST` (`:64`) and run detail (`:44`, Q-1 = A); `app/api/health.py` fetch-log (`:43`).
- `app/services/operator.py`: CLI (`contracts/operator-cli.md`).
- `/metrics`: no code change; the existing Caddy test (`tests/infra/test_deploy_config.py`) stays.
- T: `tests/api/test_operator_gate.py` (matrix: anonymous 401 / student 403 / operator success for every gated route; no side effects on 403), `tests/services/test_operator_cli.py`, `tests/api/test_no_role_endpoints.py` (OpenAPI scan; extra `is_operator` field ignored on sign-up and profile), regression: existing `tests/api/*` run unchanged with a student token for student routes.

### US2 Safe fetching (FR-SSRF-1..8)
- New `app/core/url_safety.py`: `_resolve_host`, `_non_public`, `unsafe_url_reason` (moved verbatim) plus `resolve_public(host)` and `is_public_hostname(domain)` (IP literal, reserved/private-suffix list, single label, resolution). `app/services/candidate_validation.py` re-exports the old names.
- `app/tools/web_fetch.py`: hop loop, pinned connect, `blocked` flag, `trust_env=False`, 5 hops (`MAX_FETCH_REDIRECTS`). `app/sources/connectors/api_connector.py:64` uses the same helper with 0 redirects.
- `app/schemas/source.py:_validate_domain` extended; `app/api/sources.py` approve maps a `domain` unique violation to 409 via a new `DuplicateSourceDomainError` raised by `source_repo` (rollback first).
- `app/services/candidate_validation.py`: the post-redirect `final_url` check stays as a second check; the primary guard is now inside the fetch.
- T: `tests/tools/test_web_fetch_ssrf.py` (battery of at least 20 cases against a recording `MockTransport`: loopback, RFC1918, link-local/metadata, IPv6, mapped, `localhost`, numeric encodings, multi-address DNS with one private, redirect to private at hop 1/2/5, a 6th hop, pinned-IP assertion `request.url.host == checked IP` with `Host`/`sni_hostname` preserved, public happy path), `tests/api/test_match_url_ssrf.py` (route level, 422 `blocked:`), `tests/api/test_source_approval_domain.py` (internal.corp, 10.0.0.5, 169.254.169.254, resolves-private, duplicate → 409), additions to `tests/services/test_candidate_validation.py` (a redirect hop is never requested).

### US3 Least-privilege DB (FR-DBROLE-1..6)
- `app/core/config.py`: `migration_database_url`; `migrations/env.py` prefers it.
- Start-up check in the `app/main.py` lifespan: query `pg_roles` for the connected role (`rolsuper`, `rolcreatedb`, `rolcreaterole`, `rolbypassrls`) and `has_schema_privilege(current_user,'public','CREATE')`; log a warning if any is true. Never blocks start.
- `backend/scripts/db/create_app_role.sql` (copy of the contract) and a new "Database roles" section in `docs/operations-hardening.md`.
- CI: both workflows create the role, migrate with `MIGRATION_DATABASE_URL`, test with the restricted `DATABASE_URL`.
- T: `tests/security/test_db_role.py` (as app role: `CREATE TABLE`, `ALTER TABLE`, `DROP TABLE`, `CREATE ROLE` all fail; skips with a reason when connected as owner); the whole suite under the restricted role in CI; `tests/security/test_audit_append_only.py` (UPDATE/DELETE on `audit_log` refused).

### US4 Credentials (FR-CRED-1..5)
- `app/schemas/auth.py`: password and email validators (sign-up strict, login lenient). `app/core/security.py`: `hash_password` rejects more than 72 bytes. `app/api/auth.py`: lower-cased lookups through `func.lower`, `IntegrityError` → 409.
- `app/core/config.py`: secret `model_validator`. `.env.example` keeps the placeholder (it is the value that must be refused).
- Update `ci.yml`/`deep-ci.yml` secret to 32 or more characters and the two tests that set `"test-secret"`.
- Pre-check query in the runbook; the migration creates the `lower(email)` index.
- T: `tests/api/test_auth_credentials.py` (0/7/8/72/73 bytes, 25 four-byte characters, case-variant duplicate, old mixed-case account logs in with any case, legacy long-password login), `tests/core/test_config_secret.py` (31/32/placeholder), a two-session concurrent sign-up test.

### US5 Uploads (FR-UPLOAD-1..3)
- `app/core/uploads.py`: `read_pdf_upload(file, max_bytes)` (documents: `%PDF-` within the first 1024 bytes, else 415) and `read_cv_upload(file, max_bytes)` (PDF by content, else strict `bytes.decode("utf-8")` with no NUL byte, else 415 `Unsupported file: send a PDF or a UTF-8 plain-text file`); both read at most `max_bytes + 1` bytes; `app/core/body_limit.py`: ASGI middleware for the two paths, registered in `app/main.py` beside the existing middleware; `Caddyfile` `request_body` (decimal `11MB` = 11 000 000 bytes leaves room above 10 MiB + the 64 KiB multipart allowance).
- `app/api/documents.py:57` uses `read_pdf_upload`; `app/api/profile.py:82-87` uses `read_cv_upload`, keeps the text branch of `_extract_text_from_upload` but strict (no `errors="ignore"`), and `import_cv` becomes `def`; `doc_pipeline/graph.py:113-117` stops using the filename.
- T: `tests/api/test_upload_limits.py` (exact 10 MiB, +1 byte, document upload: renamed text file refused and PDF with an odd name accepted; CV import: valid UTF-8 `.txt` accepted, Latin-1 text and binary refused, PDF recognised by content; empty, corrupt, encrypted, truncated; nothing stored, no row), `tests/api/test_import_cv_nonblocking.py` (async client, slow fake extractor, `GET /health` under 1 s), `tests/core/test_body_limit.py` (chunked body without `Content-Length`), Caddyfile assertion in `tests/infra/test_deploy_config.py`.

### US6 Audit log (FR-AUDIT-1..4)
- `app/models/audit.py`, `app/data/repositories/audit_repo.py` (`record(db, user_id, action, entity)`, closed action set), calls at the six points of research R9; `monitoring_repo.create_run_if_none_running(..., actor_id=None)`.
- T: `tests/api/test_audit_log.py` (each action once with the right actor, entity and time; 403/409/422 leave nothing; a failing audit insert aborts the action), `tests/api/test_no_audit_route.py` (OpenAPI scan), a scheduled run writes no audit row.

### US7 Recovery (FR-RECOV-1, -2)
- Rollback helper `safe_rollback(db)` in `app/data/repositories/db.py`, used at `official_fetch.py:308`, `discovery/agent.py:285`, `ingestion/graph.py:133` and on the retryable-failure path before `_log_failure`.
- T: fault-injection tests for the three sites (raise a DB error from the inner call; assert the next write and, for discovery, the closing `get_coverage_summary`, succeed) and `tests/security/test_broad_catch_rollback.py` (AST: every `except Exception` in `app/` has a rollback or an allow-list entry with a reason; allow-list from research R3).

### US8 Mail (FR-MAIL-1..3)
- `app/services/alerts.py:116-143`: `ssl.create_default_context()` for STARTTLS, `logger.warning` on skip and failure, reason codes in `email_error`. `.env.example` documents the Gmail values as comments with empty placeholders.
- T: `tests/services/test_alerts_email.py` (configured → one message and a verifying context passed to `starttls`; unconfigured → alert present and a log line; send failure → alert present, no password in log or `email_error`, run not failed), `tests/test_no_secrets_in_repo.py` extended with an App-Password-shaped pattern.

### US9 Deploy (FR-DEPLOY-1..3)
- `docs/operations-hardening.md`: new "Database roles" and "Deploy 010" sections; §13 record; `docs/vm-deployment-notes.md` pointer. `quickstart.md` is the source text. Post-deploy checks 1-6 and 8 as `backend/scripts/post_deploy_check.py` (operator token and ONE fixed check account via environment variables: it signs up on the first run and logs in on later runs, so no new account is created per run; prints PASS/FAIL per check); check 7 (email) is manual.
- T: `tests/infra/test_deploy_config.py` additions (compose has no owner URL; Caddyfile has the body limit and still 404s `/metrics`); the checker script has its own unit test against a fake server.

## Entry-point coverage (FR-PROC-1)

Every row of the spec's entry-point map has at least one test above that starts at that route, the CLI `main(argv)`, the scheduler tick function, or application start-up. Nothing is added as a helper that no entry reaches: `url_safety` is called by `fetch_url` and the approval schema; `uploads` by two routes; `audit_repo` by the six write points; `safe_rollback` by four handlers.

## Project Structure

### Documentation (this feature)

```text
specs/010-security-hardening/
├── spec.md
├── plan.md              # this file
├── research.md          # R1-R10 + findings F
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── api-delta.md
│   ├── operator-cli.md
│   └── db-role.sql
├── checklists/requirements.md
└── tasks.md             # /sp.tasks (not created here)
```

### Source Code (existing backend layout, changes only)

```text
backend/
├── app/
│   ├── api/            deps.py sources.py monitoring.py health.py auth.py profile.py documents.py
│   ├── core/           config.py security.py url_safety.py(new) uploads.py(new) body_limit.py(new)
│   ├── data/repositories/  audit_repo.py(new) source_repo.py monitoring_repo.py application_repo.py db.py
│   ├── models/         user.py audit.py(new)
│   ├── schemas/        auth.py source.py
│   ├── services/       operator.py(new) alerts.py candidate_validation.py
│   ├── sources/connectors/  official_fetch.py api_connector.py
│   ├── agents/discovery/agent.py
│   ├── tools/          web_fetch.py
│   ├── workflows/      ingestion/graph.py doc_pipeline/graph.py
│   └── main.py
├── migrations/         env.py, versions/<new revision>
├── scripts/            db/create_app_role.sql, post_deploy_check.py
└── tests/              security/ (new), api/, core/, services/, tools/, infra/
Caddyfile, .env.example, .github/workflows/ci.yml, deep-ci.yml, docs/operations-hardening.md
```

**Structure Decision**: keep the existing modular-monolith layout; new shared code goes in `app/core` (no layer imports upward: `tools` no longer needs `services`).

## Risks (max 3) and guardrails

1. **The restricted role breaks something only the owner role could do** (a missing grant, a DDL-like statement). Guardrail: CI runs the whole suite as `scholarship_app`; the deploy switches `.env` last and rollback is a one-line `.env` revert.
2. **Pinned-IP fetch changes behaviour for real sites** (SNI, HTTP/2, IPv6-only hosts, virtual hosting). Guardrail: `Host` and `sni_hostname` are tested; the post-deploy checklist includes a manual run that fetches the two live monitored sources; a fallback to a checked-but-unpinned connect is not built and would need the owner's approval.
3. **The case-insensitive email migration meets a collision in live data.** Guardrail: read-only pre-check; the unique-index creation fails closed; the deploy halts (FR-CRED-5).

## Records to update (text only; not applied here)

The manager edits the Blueprint and PRD; the ADR comes from `/sp.adr`. Exact changes required by D-1..D-4 (including Q-1 = A):

### Master Blueprint (`specs/scholarship-ai-assistant-master-blueprint-v1.md`), version 1.8 → 1.9

1. **§19, last sentence of the "Implementation status" paragraph** — replace
   `The SSRF *redirect* residual risk accepted in ADR-0010 / ADR-0011 stays accepted for candidate validation.`
   with
   `*(Amended 2026-10-29, v1.9, D-1, ADR-0016):* the SSRF redirect residual risk accepted in ADR-0010 / ADR-0011 is **closed**: every fetch of a user-supplied or candidate URL checks the address of the first URL and of every redirect hop (5 hops at most) before any request is sent to it, and connects only to an address that passed the check.`
2. **§22, row 1 (010 security-hardening), scope cell** — replace `Operator role and the **minimal** operator restriction on source approve/reject and monitoring runs (moved here from 004); address check on user-supplied URLs (URL match, source approval)` with `Operator role (one operator, the owner; granted only by a server command) restricting source approve/reject, manual monitoring runs, source fetch-log reads, candidate-validation reads the per-run source outcomes (monitoring run detail); address check on every hop of every fetch of a user-supplied or candidate URL (URL match, source approval, candidate validation); public host name required at source approval`. Keep the rest of the row (least-privilege role, password rules, upload limits, `audit_log`, rollback, SMTP, deploy).
3. **§27 A17** — append: `*(v1.9)* the restriction covers approve, reject, manually started monitoring runs, source fetch-log reads, monitoring run detail (per-source outcomes) and candidate-validation reads; operator status is granted and revoked only by a command on the server.` **A20** — change `the **minimal** operator restriction of A17 moves from 004 to 010` to `the operator restriction of A17, as widened in v1.9, moves from 004 to 010`.
4. **§14, line `audit_log(id, user_id, action, entity, at)`** — add the note: `Actions recorded: user.signup, source.approve, source.reject, monitoring.run.manual, submission.approve, operator.grant. Append-only for the application role. user_id is the actor, or the grantee for operator.grant.`
5. **§47 "NOT YET DEFINED" item 5** — mark decided: `SMTP provider: the owner's Gmail account, smtp.gmail.com port 587 (STARTTLS, verified certificate), authenticated with a Google App Password held only in the VM environment. Least-privilege database role: created with SQL (not the Neon console) as scholarship_app, used by the app over the pooled connection; migrations run as the owner over the direct connection.`
6. **New §48 "v1.9 Revision Notes"** — table of the changes above (source: spec 010, D-1..D-4), a consistency audit (no requirement removed; Constitution unchanged; agent count stays 5), and the header theme line.

### PRD (`specs/scholarship-ai-assistant-prd-v1.md`), version 1.2 → 1.3

1. **FR-DISC-8** — append: `*(v1.3, OD-16)* Reading a source's fetch log, a monitoring run's per-source outcomes (run detail) and a candidate's validation results is also restricted to operators. Operator status is granted only by a command run on the server.`
2. **NFR-SEC-4** — replace `it shall not reach private, loopback, link-local or cloud-metadata addresses` with `it shall not reach private, loopback, link-local, reserved or cloud-metadata addresses, including through redirects: every redirect hop is checked before a request is sent to it`.
3. **NFR-SEC-7** — replace the list with `(sign-up, source approval/rejection, manually started monitoring runs, submission approvals, granting operator status)`.
4. **Open-decisions table** — add `| OD-16 (v1.3, 2026-10-29) | Security hardening scope fixed by the owner: redirect-aware address check including candidate validation; operator-only reads of fetch logs, monitoring run detail and candidate validations; operator grants audited; Gmail (App Password) as the mail provider | §9, §14 FR-DISC-8, §26 NFR-SEC-4, NFR-SEC-7 (ADR-0016) |` and the matching revision-notes rows.

### ADRs (text for `/sp.adr`; existing ADRs get a dated amendment line only)

- **New ADR-0016 "Security hardening: redirect-safe fetch, operator-only governance, least-privilege database role, audit log"** — one cluster, as ADR-0015 did: (1) fetch pins the checked address and checks every hop, superseding the residual risk of ADR-0010 and ADR-0011 Q4; (2) one operator, a boolean on the user row, changed only by a server command, with the widened operator-only set (approve, reject, manual run, run detail, fetch log, validations; D-2); (3) SQL-created `scholarship_app` role on the pooled URL, migrations as owner on the direct URL, default privileges for future tables; (4) `audit_log` written in the action's transaction, append-only by privilege, `operator.grant` included (D-3); (5) Gmail SMTP with an App Password (D-4). Alternatives to record: console-created role, unpinned per-hop check, role claim in the token, audit read API.
- **ADR-0010**: add `## Amendment (feature 010, ADR-0016)` — negative consequences (a) and (b) are closed by the per-hop, pinned fetch; the "Alternatives: modify `fetch_url`" item is now done.
- **ADR-0011**: Q4 gets `Superseded by ADR-0016 (2026-10-29)`; Q5 unchanged.
- **ADR-0015**: decision 1's last sentence (`The SSRF redirect risk accepted in ADR-0010 and ADR-0011 stays accepted.`) and the matching Negative-consequences bullet get `Superseded by ADR-0016 (owner decision 2026-10-29).`

### Other documents to touch during implementation (not decisions)
`docs/operations-hardening.md` §11 (known limitations: remove the SSRF redirect row), §13 (deployment record), the new database-roles section; `specs/charter.md` Status line when 010 ships; `docs/full-project-audit.md` rows get a closing note when the feature is done, as for earlier features.

## ADR suggestion

📋 Architectural decision detected: security-hardening cluster (redirect-safe fetch with a pinned address, single-operator model changed only from the server, least-privilege database role with split URLs, append-only audit log, Gmail SMTP). Document reasoning and tradeoffs? Run `/sp.adr security-hardening-decisions` (proposed number 0016). Not created; waiting for your consent.

## Post-design Constitution re-check

No design choice above touches agent boundaries, matching, submission or registry authority except to tighten them. The only new persistent structure is `audit_log` plus a column and an index on `users`. Gate: **pass**.

## Complexity Tracking

No violations to justify.
