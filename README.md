# Scholarship AI Assistant

An agentic FastAPI backend that helps students discover and apply for scholarships.

## Running the backend

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload
```

The API will be available at `http://127.0.0.1:8000`, with a health check at `GET /health`.

## Running tests

```bash
cd backend
uv run pytest
```

Always run tests via `uv run pytest` (or `.venv/Scripts/python.exe`/`.venv/bin/python`), not a system `python` — this project requires 3.12.x, and running with a different interpreter (e.g. a system 3.14) produces misleading SQLAlchemy collection errors that look like real test breakage.

### Safe local test runs

Most tests need a Postgres database, and they create and delete rows. Never point them at a shared database (such as the Neon instance in `.env`).

**Offline (no database, no network).** Runs only the tests that need neither, and blocks every non-loopback socket and DNS lookup, so a leak fails loudly. The values below are dummies; environment variables beat `.env`.

```bash
cd backend
export DATABASE_URL=postgresql+psycopg://nobody:nopass@127.0.0.1:1/none  # allow-secret
export GEMINI_API_KEY=not-a-real-key QDRANT_URL=http://127.0.0.1:1 QDRANT_API_KEY=x
uv run pytest --offline -q -p no:cacheprovider
```

**Full suite, exactly like CI, on a throw-away Postgres** (use `podman` in place of `docker` if that is what you have). Environment variables beat `.env`, so this cannot reach Neon, Qdrant or Gemini.

```bash
docker run -d --name test-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=scholarship_ci -p 5432:5432 postgres:16-alpine
cd backend
export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/scholarship_ci JWT_SECRET_KEY=dummy-ci-secret-not-real  # allow-secret
export GEMINI_API_KEY=not-a-real-key QDRANT_URL=http://127.0.0.1:1 QDRANT_API_KEY=x
uv run alembic upgrade head && uv run pytest -q -p no:cacheprovider -rfE
docker rm -f test-pg
```

**Shared-database rule.** If `DATABASE_URL` points to a host other than `localhost`, `127.0.0.1` or `::1`, every DB-dependent test skips with "DATABASE_URL points to a shared database; set ALLOW_SHARED_DB=1 to run DB tests against it". Set `ALLOW_SHARED_DB=1` only when you really mean to run the suite against that database. CI uses a localhost Postgres service and is unaffected.
