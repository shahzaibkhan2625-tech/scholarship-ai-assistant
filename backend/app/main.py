import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.application import router as application_router
from app.api.auth import router as auth_router
from app.api.discovery import router as discovery_router
from app.api.documents import router as documents_router
from app.api.generation import router as generation_router
from app.api.matching import router as matching_router
from app.api.monitoring import router as monitoring_router
from app.api.profile import router as profile_router
from app.api.qa import router as qa_router
from app.api.sources import router as sources_router
from app.api.url_match import router as url_match_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """002: on startup fail any run left `running` by a crashed process, then
    start the monitoring scheduler (a no-op when MONITORING_INTERVAL_MINUTES
    <= 0, which is how tests keep it off); stop it on shutdown. Recovery is
    best-effort so an unreachable DB cannot stop the API from booting."""
    from app.data.repositories import monitoring_repo
    from app.data.repositories.db import SessionLocal
    from app.scheduling.scheduler import start_scheduler, stop_scheduler

    try:
        with SessionLocal() as session:
            recovered = monitoring_repo.fail_leftover_running_runs(session)
        if recovered:
            logger.warning("marked %d leftover running monitoring run(s) as failed", recovered)
    except Exception:  # noqa: BLE001
        logger.exception("monitoring crash recovery failed")

    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()


app = FastAPI(title="Scholarship AI Assistant", lifespan=lifespan)

app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(profile_router, prefix="/profile", tags=["profile"])
app.include_router(url_match_router, prefix="/scholarships", tags=["url-match"])
app.include_router(matching_router, tags=["matching"])
app.include_router(qa_router, prefix="/scholarships", tags=["qa"])
app.include_router(sources_router, tags=["sources"])
app.include_router(discovery_router, prefix="/discovery", tags=["discovery"])
app.include_router(application_router, tags=["application"])
app.include_router(documents_router, tags=["documents"])
app.include_router(generation_router, tags=["generation"])
app.include_router(monitoring_router, tags=["monitoring"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
