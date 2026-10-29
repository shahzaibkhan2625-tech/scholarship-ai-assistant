# API Contract Delta: Security Hardening (010)

Changes to the existing HTTP API. **No new routes.** Paths, methods and success bodies are unchanged except where stated. All error bodies keep the existing FastAPI shape `{"detail": ...}` unless noted.

## New status classes

| Status | When | Body |
|---|---|---|
| 403 Forbidden | signed-in user is not an operator and calls an operator-only route | `{"detail": "Operator access required"}` |
| 413 Payload Too Large | upload body or file over 10 MiB | `{"detail": "File too large (maximum 10 MB)"}` |
| 415 Unsupported Media Type | upload is not a PDF by content | `{"detail": "Only PDF files are accepted"}` |

401 "Could not validate credentials" is unchanged and still wins for missing/invalid tokens (the operator check runs after authentication).

## 1. Operator-only routes (FR-AUTHZ-1, -2) — entry points for US1

| Route | Before | After | Notes |
|---|---|---|---|
| `POST /sources/candidates/{candidate_id}/approve` | any user | operator; 403 otherwise | also US2/US6 below |
| `POST /sources/candidates/{candidate_id}/reject` | any user | operator; 403 otherwise | audited |
| `POST /monitoring/runs` | any user | operator; 403 otherwise | audited |
| `GET /sources/{source_id}/fetch-log` | any user | operator; 403 otherwise | |
| `GET /sources/candidates/{candidate_id}/validations` | any user | operator; 403 otherwise | |
| `GET /monitoring/runs/{run_id}` | any user | operator; 403 otherwise (Q-1 = A, 2026-10-29: `source_outcomes` returns raw fetch-log rows) | |

Unchanged and open to every signed-in user: `GET /sources`, `GET /sources/candidates`, `GET /sources/health`, `GET /monitoring/runs`, `GET /coverage`, and every student route. `/metrics` stays answered 404 by the public listener (Caddyfile) and bearer-protected in the app.

OpenAPI: the five routes gain `403` in `responses`. No operator-management route exists (FR-AUTHZ-3); a test asserts no path under the app contains `operator`, `role` or `admin`, and that sign-up/profile bodies ignore an extra `is_operator` field.

## 2. `POST /auth/signup`, `POST /auth/login` (FR-CRED-1, -2, -4)

- Request `email`: trimmed and lower-cased before use (both routes).
- Sign-up `password`: 8 characters minimum and 72 bytes maximum (UTF-8). Violations → **422** with the standard validation error body, one message per rule: `Password must be at least 8 characters` / `Password must be at most 72 bytes`.
- Login `password`: no length rule (existing accounts).
- Sign-up with an address equal to an existing one ignoring case → **409** `Email already registered` (also for the concurrent-request race).
- Response `UserResponse.email` is the stored (lower-case) value.
- Audit: successful sign-up writes `user.signup`.

## 3. `POST /sources/candidates/{candidate_id}/approve` (FR-SSRF-6, -7)

Request schema unchanged (`SourceRegistryCreate`). New outcomes:

| Status | Condition |
|---|---|
| 422 | `domain` is an IP address, is on the reserved/private-suffix list (IANA special-use names plus `internal`, `corp`, `lan`, `home`, `intranet`, `private`, `localdomain`), is `localhost`-like, or resolves to a non-public address. Message names the rule, e.g. `domain must be a public host name (resolves to non-public address 10.0.0.5)` |
| 409 | `domain` already in the registry (today an unhandled unique violation → 500). The candidate stays `pending`. Existing 409 "already reviewed" is unchanged |

Audit: success writes `source.approve` in the same transaction as the promotion.

## 4. `POST /scholarships/match-url` (FR-SSRF-1..5, -8)

Success and the existing `422 FetchFailure` shape are unchanged. A refused URL (non-public address at any hop, scheme other than http/https, more than 5 redirects) returns the same **422 `FetchFailure`** with `error` starting `blocked:` (for example `blocked: host resolves to non-public address 169.254.169.254`). No request is sent to the refused address. A log line records user, entry point and reason.

## 5. Uploads (FR-UPLOAD-1..3)

| Route | Change |
|---|---|
| `POST /applications/{application_id}/documents` | 413 over 10 MiB; 415 if not a PDF by content (first 1024 bytes must contain `%PDF-`); 400 `File is empty`; corrupt/encrypted → existing 422 (unchanged); a real PDF with any file name is accepted |
| `POST /profile/import-cv` | same 413 / 415 / 400; corrupt or encrypted PDF → **422** (was 500); a non-PDF is no longer decoded as text; runs in the worker thread pool so it never blocks other requests |

Nothing is stored and no row is created when an upload is refused. Exactly 10 MiB (10 485 760 bytes) is accepted; 10 485 761 is refused.

## 6. Alerts (FR-MAIL)

`GET /alerts` response shape unchanged. `email_error` now holds a short code (`not_configured`, `send_failed`, `no_recipient`) instead of raw exception text; details go to the server log.

## 7. Audit records (FR-AUDIT-4)

No route returns audit records. A test asserts the OpenAPI document has no path containing `audit`. The owner reads the table with owner credentials (runbook).
