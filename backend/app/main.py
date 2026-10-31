import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.alerts import router as alerts_router
from app.api.coverage import router as coverage_router
from app.api.application import router as application_router
from app.api.auth import router as auth_router
from app.api.discovery import router as discovery_router
from app.api.documents import router as documents_router
from app.api.generation import router as generation_router
from app.api.health import router as health_router
from app.api.matching import router as matching_router
from app.api.monitoring import router as monitoring_router
from app.api.profile import router as profile_router
from app.api.qa import router as qa_router
from app.api.scholarships import router as scholarships_router
from app.api.sources import router as sources_router
from app.api.url_match import router as url_match_router
from app.core.body_limit import BodyLimitMiddleware
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.metrics import router as metrics_router
from app.core.rate_limit import RateLimitMiddleware, TokenBucketLimiter
from app.core.request_id import RequestIDMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """002: configure JSON logging (after uvicorn has set up its own loggers, so ours
    wins), warn if the DB role is owner-level (FR-DBROLE-5), fail any run left `running` by a crashed process, then
    start the monitoring scheduler (a no-op when MONITORING_INTERVAL_MINUTES
    <= 0, which is how tests keep it off); stop it on shutdown. Recovery is
    best-effort so an unreachable DB cannot stop the API from booting."""
    from app.core import db_role_check
    from app.data.repositories import monitoring_repo
    from app.data.repositories.db import SessionLocal
    from app.scheduling.scheduler import start_scheduler, stop_scheduler

    configure_logging()
    try:
        with SessionLocal() as session:
            recovered = monitoring_repo.fail_leftover_running_runs(session)
        if recovered:
            logger.warning("marked %d leftover running monitoring run(s) as failed", recovered)
    except Exception:  # noqa: BLE001
        logger.exception("monitoring crash recovery failed")

    try:
        db_role_check.check_current_db_role()
    except Exception:  # noqa: BLE001
        logger.warning("database role check failed")

    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()


app = FastAPI(title="Scholarship AI Assistant", lifespan=lifespan)
# Per-process limiter (ADR-0011): state is in memory, so the limit is per worker. Tests reset it.
rate_limiter = TokenBucketLimiter(settings.rate_limit_per_minute)
# Starlette makes the LAST middleware added the outermost: the limiter is added first so
# RequestIDMiddleware wraps it and even a 429 carries an X-Request-ID and an access-log line.
app.add_middleware(RateLimitMiddleware, limiter=rate_limiter)
app.add_middleware(BodyLimitMiddleware)
app.add_middleware(RequestIDMiddleware)

app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(profile_router, prefix="/profile", tags=["profile"])
app.include_router(url_match_router, prefix="/scholarships", tags=["url-match"])
app.include_router(matching_router, tags=["matching"])
app.include_router(qa_router, prefix="/scholarships", tags=["qa"])
# Before sources_router so /sources/health is never captured by a /sources/{...} path param.
app.include_router(health_router, tags=["health"])
app.include_router(sources_router, tags=["sources"])
app.include_router(discovery_router, prefix="/discovery", tags=["discovery"])
app.include_router(application_router, tags=["application"])
app.include_router(documents_router, tags=["documents"])
app.include_router(generation_router, tags=["generation"])
app.include_router(monitoring_router, tags=["monitoring"])
app.include_router(alerts_router, tags=["alerts"])
app.include_router(coverage_router, tags=["coverage"])
app.include_router(scholarships_router, tags=["scholarships"])
app.include_router(metrics_router, tags=["metrics"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
