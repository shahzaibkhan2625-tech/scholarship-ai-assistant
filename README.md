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
