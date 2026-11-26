import logging
from contextlib import asynccontextmanager

from datetime import datetime, timedelta, timezone
from math import ceil

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

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
from app.core.llm_gate import LlmError, install_gate
from app.core.llm_priority_middleware import LlmPriorityMiddleware
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

    # 003: the budget gate is installed after the start-up checks; with none installed every provider
    # call is refused (fail closed).
    from app.services.llm_budget import BudgetConfig, DbLlmGate

    install_gate(DbLlmGate(BudgetConfig.from_settings(settings)))

    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()
        install_gate(None)


app = FastAPI(title="Scholarship AI Assistant", lifespan=lifespan)
# Per-process limiter (ADR-0011): state is in memory, so the limit is per worker. Tests reset it.
rate_limiter = TokenBucketLimiter(settings.rate_limit_per_minute)
# Starlette makes the LAST middleware added the outermost: the limiter is added first so
# RequestIDMiddleware wraps it and even a 429 carries an X-Request-ID and an access-log line.
# 003: sets the `user_facing` LLM class for every request. Added first so it is the innermost: a
# request refused by the rate limiter or the body limit never reaches the gate.
app.add_middleware(LlmPriorityMiddleware)
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


@app.exception_handler(LlmError)
async def llm_temporarily_unavailable_handler(_request: Request, exc: LlmError) -> JSONResponse:
    """FR-BUDGET-5 and FR-ERR-6, contracts/api-delta.md section 1: a student request that is refused or that
    meets ANY provider failure (quota, outage, unparseable reply, key not configured) is a 503 with
    `Retry-After`, never a bare 500. The body holds no secret, provider payload or stack trace: only the
    short `reason` class the exception carries. Only the `LlmError` family is mapped. With no provider time,
    the retry time is now plus `llm_unavailable_retry_seconds`."""
    provider_time = getattr(exc, "retry_at", None)
    if provider_time is None:
        provider_time = datetime.now(timezone.utc) + timedelta(seconds=settings.llm_unavailable_retry_seconds)
    retry_at = provider_time.astimezone(timezone.utc)
    seconds = max(1, ceil((retry_at - datetime.now(timezone.utc)).total_seconds()))
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": str(seconds)},
        content={
            "detail": "AI features are temporarily unavailable. Try again after the retry time.",
            "code": "llm_temporarily_unavailable",
            "retry_after_seconds": seconds,
            "retry_at": retry_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "reason": exc.reason,
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
