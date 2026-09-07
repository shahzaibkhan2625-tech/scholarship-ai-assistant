from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.matching import router as matching_router
from app.api.profile import router as profile_router
from app.api.qa import router as qa_router
from app.api.url_match import router as url_match_router

app = FastAPI(title="Scholarship AI Assistant")

app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(profile_router, prefix="/profile", tags=["profile"])
app.include_router(url_match_router, prefix="/scholarships", tags=["url-match"])
app.include_router(matching_router, tags=["matching"])
app.include_router(qa_router, prefix="/scholarships", tags=["qa"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
