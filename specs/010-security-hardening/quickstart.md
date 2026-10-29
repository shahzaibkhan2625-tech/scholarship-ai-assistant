# Quickstart: Security Hardening (010)

Two audiences: a developer verifying the feature locally/CI, and the owner deploying to the VM. Commands run from `backend/` unless stated. `uv` is the project's runner.

## A. Local / CI verification

1. **Settings** (`.env` at repo root, never committed): `JWT_SECRET_KEY` must now be at least 32 characters and not the sample placeholder, or the app, Alembic and the CLI refuse to start.
2. **Two database URLs** (local Postgres or a throwaway Neon branch):
   ```text
   MIGRATION_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/scholarship_ci   # owner
   DATABASE_URL=postgresql://scholarship_app:<pw>@localhost:5432/scholarship_ci          # restricted
   ```
3. **Create the role, migrate as owner, test as the app role** (this is also the CI sequence):
   ```text
   psql "<owner url>" -v migration_role='postgres' -f ../specs/010-security-hardening/contracts/db-role.sql   # prompts for the app role's password (\password)
   uv run alembic upgrade head          # uses MIGRATION_DATABASE_URL
   psql "<owner url>" -v migration_role='postgres' -f ../specs/010-security-hardening/contracts/db-role.sql   # re-run: picks up new tables, locks audit_log (prompt again; Ctrl-C keeps the password)
   uv run pytest                        # uses DATABASE_URL (restricted)
   ```
4. **Operator**: `uv run python -m app.services.operator grant --email you@example.com`, then sign in and call `POST /monitoring/runs` (202); a second account gets 403.

### Acceptance walk-through (maps to the spec's stories)

| Story | Try this | Expect |
|---|---|---|
| US1 | non-operator `POST /sources/candidates/{id}/approve` | 403, candidate still `pending` |
| US1 | `POST /auth/signup` with extra `"is_operator": true` | account created, not an operator |
| US2 | `POST /scholarships/match-url` with `http://169.254.169.254/latest/meta-data/` | 422 `blocked: …non-public address 169.254.169.254` |
| US2 | URL that 302s to `http://10.0.0.5/` | 422 `blocked`, no request to 10.0.0.5 (fake-network test) |
| US2 | approve with domain `internal.corp` / `10.0.0.5` | 422; duplicate domain → 409 |
| US3 | as `scholarship_app`: `CREATE TABLE x(a int);` | `permission denied for schema public` |
| US4 | sign up with 7-char, 73-byte password; `Ada@x.com` then `ada@x.com` | 422; 409 |
| US4 | start with `JWT_SECRET_KEY=short` | process exits with a clear message, secret not printed |
| US5 | document upload: 10 485 760 B PDF / 10 485 761 B / renamed text file | 201 / 413 / 415 |
| US5 | CV import: valid UTF-8 `.txt` / Latin-1 text file / binary file | 200 / 415 / 415 |
| US5 | slow CV import running, `GET /health` meanwhile | answered < 1 s |
| US6 | sign up, approve, reject, manual run, submission approval, grant | one `audit_log` row each (owner `SELECT`), none on refused calls |
| US7 | inject a DB error in candidate detection | run completes; next log write succeeds |
| US8 | fake SMTP server | one message; with `SMTP_HOST` unset the alert is in `GET /alerts` and a log line says why |

## B. Deploy to the VM (owner, in this order)

Pre-flight (read-only):
1. `git -C <repo> branch --show-current` → `main`; merged feature present; `git log -1 --oneline`.
2. Email collision check (FR-CRED-5), with owner credentials:
   `SELECT lower(email), count(*) FROM users GROUP BY 1 HAVING count(*) > 1;` → must return no rows. Any row → stop and tell the manager.
3. Keep rollback material: `cp .env .env.pre-010`; note the current image id (`sudo podman images`).
4. **Signing secret (before the migration step)**: the new image's Alembic and CLI refuse to start on a weak secret. Check the length only, never the value: `awk -F= '/^JWT_SECRET_KEY=/{print length($2)}' .env` must print 32 or more, and the value must not be `change-me-to-a-random-secret`. If not, put a new random value in `.env` now (for example `openssl rand -hex 32`). Changing it signs out existing sessions; there are 0 users, so nothing is lost.

Roll-out:
1. **Create the role** (from an interactive terminal on a machine with psql). Read the owner **direct** URL silently into a shell variable, run the script, then unset it; no secret is typed in a command:
   ```text
   read -rs OWNER_DIRECT_URL
   psql "$OWNER_DIRECT_URL" -v migration_role='neondb_owner' -f contracts/db-role.sql   # \password prompts for the new app password
   unset OWNER_DIRECT_URL
   ```
   Put the app password you typed at the prompt into the VM `.env` (step 4) and nowhere else.
2. **Build, do not swap**: `sudo /usr/local/bin/podman-compose -f docker-compose.podman.yml build backend`.
3. **Migrate as the owner, passing the URL by variable name only** (the value is never typed into a command, never written to `.env`, and exists only in this shell and the `--rm` container):
   ```text
   read -rs MIGRATION_DATABASE_URL            # owner DIRECT url, not echoed
   export MIGRATION_DATABASE_URL
   sudo --preserve-env=MIGRATION_DATABASE_URL podman run --rm --env-file .env -e MIGRATION_DATABASE_URL <new image> uv run alembic upgrade head
   sudo --preserve-env=MIGRATION_DATABASE_URL podman run --rm --env-file .env -e MIGRATION_DATABASE_URL <new image> uv run alembic current   # shows the new head
   unset MIGRATION_DATABASE_URL
   ```
   (`sudo` resets the environment by default, hence `--preserve-env` for that one variable. `-e NAME` without a value makes podman copy it from the caller's environment.) Then re-run `db-role.sql` as in step 1 so the new tables are granted and `audit_log` is append-only.
4. **Edit `.env`**: `DATABASE_URL` = `scholarship_app` on the **pooled** (`-pooler`) host; `JWT_SECRET_KEY` already set in pre-flight step 4 (keep it); `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USERNAME`, `SMTP_PASSWORD` (Google App Password, requires 2-Step Verification), `ALERTS_FROM_EMAIL` (same Gmail). `chmod 600 .env`.
5. **Caddy**: `request_body { max_size 11MB }` is in the repo `Caddyfile`; validate with `caddy validate` as in the runbook, then recreate containers (runbook §10 step 3 order: stop/remove Caddy, then backend, then `up -d`).
6. **Owner account, then operator.** The production database has 0 users, so first **the owner signs up their own account through the API** (`POST /auth/signup` with the owner's email and a password of 8 to 72 bytes). Then grant: `sudo podman exec scholarship-ai-assistant_backend_1 uv run python -m app.services.operator grant --email <owner>` (expect `granted operator status to <owner>`; `no such user` means the sign-up was not done).

Post-deploy checks (record the result in runbook §13; any failure → rollback):
1. `curl -sk https://<site>/health` → 200; `uv run alembic current` = head.
2. Password rules with the ONE fixed check account (`CHECK_ACCOUNT_EMAIL`/`CHECK_ACCOUNT_PASSWORD`): a 7-character password → 422; the fixed account signs up on the first run (201) and on later runs sign-up returns 409 and the script logs in instead, so no new account is created per run.
3. The fixed check account (a non-operator) `POST /monitoring/runs` and `GET /monitoring/runs/{run_id}` → 403; owner account `POST /monitoring/runs` → 202 (then no second run needed). `GET /monitoring/runs` (list) → 200 for both.
4. `POST /scholarships/match-url` with the metadata address → 422 `blocked`.
5. As `scholarship_app`: `CREATE TABLE zz_check(a int)` → permission denied. Startup log has no "owner-level credentials" warning.
6. `curl -sk https://<site>/metrics` → 404.
7. Trigger or wait for one alert for the owner's profile (or use the runbook's test-alert command) → email in the owner's inbox within 5 minutes.
8. Exactly one operator, the owner (owner credentials): `SELECT email FROM users WHERE is_operator;` → exactly one row, the owner's email. Zero rows (grant skipped) or more than one → fail.

Rollback: restore `.env.pre-010`, `sudo podman tag <previous image id> localhost/scholarship-ai-assistant_backend:latest`, recreate the containers. The 010 migration is additive, so the previous code runs against the new schema; no downgrade is needed.
