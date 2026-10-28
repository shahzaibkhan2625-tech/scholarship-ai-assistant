# Data Model: Security Hardening (010)

All changes are **additive**. One Alembic revision (chained after the current head `f2b8d4a6c1e3`, checked with `alembic heads` on 2026-10-29) makes them; downgrade drops them. No existing column changes type; no data is rewritten.

## 1. `users` (existing) — one column, one index

| Change | Detail | Why |
|---|---|---|
| add `is_operator` | boolean, NOT NULL, server default `false` | FR-AUTHZ-1..5. Existing rows become non-operators. Nobody is made operator by the migration; the owner does it with the CLI (FR-AUTHZ-3, FR-AUTHZ-5). |
| add unique index on `lower(email)` | `CREATE UNIQUE INDEX … ON users (lower(email))` | FR-CRED-2, and it closes the sign-up race (two concurrent sign-ups differing by case). Fails if existing rows collide, which is the deliberate hard stop of FR-CRED-5. |

The existing `email` unique constraint (`app/models/user.py:15-16`) stays. New rows are stored lower-case; old rows are not rewritten (login/sign-up lookups use `lower(email)`), so nothing is lost if rollback is needed.

Operator status is read from the row on every request (`get_current_user` already loads the user, `app/api/deps.py:14-33`); the token carries no role claim, so a revoke takes effect on the next request (FR-AUTHZ-4).

## 2. `audit_log` (new, Blueprint §14: `audit_log(id, user_id, action, entity, at)`)

| Column | Type | Rules |
|---|---|---|
| `id` | UUID, primary key, default generated | |
| `user_id` | UUID, NOT NULL | The person who acted. For the CLI grant, the person who **received** operator status (approved assumption). **No foreign key**, so the record outlives an account deletion and the app role never needs to update it. |
| `action` | text, NOT NULL | One of the closed set below. |
| `entity` | text, NOT NULL | `<kind>:<uuid>` of what was affected. |
| `at` | timestamptz, NOT NULL, server default `now()` | |

Index on `(at)` and `(user_id, at)` for the owner's lookups.

**Closed action set** (checked in code and by a test): `user.signup`, `source.approve`, `source.reject`, `monitoring.run.manual`, `submission.approve`, `operator.grant`.

| Action | `user_id` | `entity` |
|---|---|---|
| `user.signup` | the new user | `user:<id>` |
| `source.approve` | the operator | `candidate_source:<candidate id>` |
| `source.reject` | the operator | `candidate_source:<candidate id>` |
| `monitoring.run.manual` | who started it | `monitoring_run:<run id>` |
| `submission.approve` | the student | `submission_approval:<approval id>` |
| `operator.grant` | the new operator | `user:<id>` |

**Append-only by privilege (FR-AUDIT-3)**: the app role has `INSERT, SELECT` on `audit_log`; `UPDATE, DELETE, TRUNCATE` are revoked by the migration (conditional on the role existing) and re-asserted by `db-role.sql`. Retention: none in this feature (rows are tiny; revisit with 004 records).

**Written only on success, in the caller's transaction** (R9): there is no separate "failure" state.

## 3. Entities that do not change

`candidate_sources`, `source_registry` (its `domain` unique constraint, `app/models/source.py:91`, is what turns a duplicate approval into a 409 once the violation is mapped), `source_fetch_log`, `candidate_source_validations`, `alerts` (field `email_error` keeps its column but now holds a short reason code, see research R7), `monitoring_runs`, `submission_approvals`, documents, profile.

## 4. Database role (not a table; in `contracts/db-role.sql`)

| Role | Created by | Rights |
|---|---|---|
| migration/owner (today `neondb_owner`) | Neon | owns every table; runs Alembic over the **direct** connection; never in the app container's environment |
| `scholarship_app` | `db-role.sql`, run by the owner | `LOGIN`, `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`; `CONNECT`; `USAGE` on schema `public`; `SELECT, INSERT, UPDATE, DELETE` on all tables and `USAGE, SELECT, UPDATE` on all sequences, **plus default privileges for objects the owner creates later**; no `CREATE`; no rights on `alembic_version`; `audit_log` limited to `INSERT, SELECT` |

## 5. Settings (configuration, not data)

| Name | Meaning | Rule |
|---|---|---|
| `DATABASE_URL` (existing) | app connection, pooled, `scholarship_app` | start-up warns if the connected role has owner-level rights (FR-DBROLE-5) |
| `MIGRATION_DATABASE_URL` (new, optional) | Alembic connection, direct, owner | passed only to the migration command; absent in the app's `.env` |
| `JWT_SECRET_KEY` (existing) | session signing | refuse to start if < 32 chars or equals `change-me-to-a-random-secret` (FR-CRED-3) |
| `SMTP_*`, `ALERTS_FROM_EMAIL` (existing) | Gmail via App Password | unchanged names; values only on the VM |
| `MAX_UPLOAD_BYTES` (new, default 10 MiB) | upload cap | constant-like; override only in tests |
| `MAX_FETCH_REDIRECTS` (new, default 5) | redirect hops | approved spec assumption |

## 6. State transitions

- Operator: `false → true` (CLI grant, audited) and `true → false` (CLI revoke, not audited per the approved list; logged). No other transition exists.
- Candidate source: unchanged (`pending → approved/rejected`), now only reachable by an operator, and approval can now also end in "domain rejected" or "domain already registered" without changing the candidate.
