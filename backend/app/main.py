from fastapi import FastAPI

from app.api.auth import router as auth_router

app = FastAPI(title="Scholarship AI Assistant")

app.include_router(auth_router, prefix="/auth", tags=["auth"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
