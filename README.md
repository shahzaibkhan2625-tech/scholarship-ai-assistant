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
