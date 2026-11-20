-- Contract for the least-privilege application role (010, FR-DBROLE-1..3, 6).
-- Run by the OWNER (migration role) over the DIRECT Neon connection, from a terminal:
--   read -rs OWNER_DIRECT_URL                      # paste the owner direct URL; nothing is echoed
--   psql "$OWNER_DIRECT_URL" -v ON_ERROR_STOP=1 -v migration_role='neondb_owner' -f db-role.sql
-- No secret is ever typed on a command line. The role is created WITHOUT a password; the
-- password is set in a separate step (below) with ALTER ROLE over stdin, so it is not in this
-- file, a psql variable, shell history or the process list.
-- Idempotent: safe to run again after adding tables. It does not touch the password.
--   read -rs APP_ROLE_PASSWORD                     # letters and digits only, 24+ characters
--   printf "ALTER ROLE scholarship_app WITH PASSWORD '%s';\n" "$APP_ROLE_PASSWORD" | psql "$OWNER_DIRECT_URL" -v ON_ERROR_STOP=1
--   unset APP_ROLE_PASSWORD OWNER_DIRECT_URL
-- (printf is a shell builtin, so the password is not in the process list; it travels over TLS
-- and Neon hashes it.) psql 14 or newer is required for Neon.
-- -v skip_password=1 is still accepted (a no-op now) so CI needs no change.
-- Sources: https://neon.com/docs/manage/roles (roles must be created with SQL to avoid
-- neon_superuser), https://www.postgresql.org/docs/current/sql-alterdefaultprivileges.html,
-- https://www.postgresql.org/docs/release/15.0/ (public schema CREATE).

-- 1. The role: log in, nothing else, no password yet. NOBYPASSRLS matters even though no
--    policies exist yet.
SELECT 'CREATE ROLE scholarship_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'scholarship_app')
\gexec

-- No password is set here. Neon rejects pre-hashed passwords (what psql's \password sends):
--   ERROR: Received HTTP code 400 from control plane: ... "Neon only supports being given plaintext passwords"
-- Set it in a separate step: ALTER ROLE scholarship_app WITH PASSWORD '<password>' over stdin (see the header).

-- 2. No one may create objects in public (older databases keep the pre-15 default).
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- 3. Connect and read/write existing data.
SELECT format('GRANT CONNECT ON DATABASE %I TO scholarship_app', current_database())
\gexec
GRANT USAGE ON SCHEMA public TO scholarship_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO scholarship_app;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO scholarship_app;

-- 4. Tables and sequences the migration role creates LATER are usable without a manual grant.
--    FOR ROLE must be the role that runs Alembic (default privileges apply to objects created by it).
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO scholarship_app',
  :'migration_role')
\gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO scholarship_app',
  :'migration_role')
\gexec

-- 5. The app never needs the migration bookkeeping table (guarded: on a fresh database, e.g. CI, it does not exist until the first migration).
DO $$
BEGIN
  IF to_regclass('public.alembic_version') IS NOT NULL THEN
    REVOKE ALL ON TABLE alembic_version FROM scholarship_app;
  END IF;
END $$;

-- 6. Append-only audit trail (table exists once the 010 migration has run).
DO $$
BEGIN
  IF to_regclass('public.audit_log') IS NOT NULL THEN
    REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_log FROM scholarship_app;
  END IF;
END $$;

-- 7. Verify (expect: all false except the first row's rolcanlogin).
SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls
FROM pg_roles WHERE rolname = 'scholarship_app';
SELECT has_schema_privilege('scholarship_app', 'public', 'CREATE') AS can_create_in_public;
