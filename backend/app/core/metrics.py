"""Prometheus metrics + the `/metrics` route (002 US8, T227, NFR-HARDEN-4, O-4, ADR-0011).

A dedicated registry (not the process-global default) so import order and test
re-imports can never double-register, and so only application metrics are exposed.
Labels are low-cardinality on purpose: `status` only, never a `source_id`.

`/metrics` requires the existing bearer auth (`get_current_user`). Any registered
user can read it — an accepted limitation (ADR-0011 Q5): signup is open, there is
no role tier, and the content is operational counts. No scraper is deployed and
tokens expire after 60 minutes, so access is manual `curl` with a fresh login.

Counters live in this process; the scheduler and the API share it (one uvicorn
worker). With several workers each would report its own counts.
"""

from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest

from app.api.deps import get_current_user
from app.models.user import User

__all__ = [
    "MONITORING_RUNS_TOTAL",
    "MONITORING_RUN_DURATION_SECONDS",
    "REGISTRY",
    "RETENTION_ROWS_DELETED_TOTAL",
    "SOURCE_FETCH_TOTAL",
    "router",
]

REGISTRY = CollectorRegistry()

MONITORING_RUNS_TOTAL = Counter(
    "monitoring_runs_total",
    "Monitoring runs finished, by final status (completed|failed).",
    ["status"],
    registry=REGISTRY,
)
SOURCE_FETCH_TOTAL = Counter(
    "source_fetch_total",
    "Per-source monitoring checks, by outcome (ok|fail|error; error = the check crashed).",
    ["status"],
    registry=REGISTRY,
)
RETENTION_ROWS_DELETED_TOTAL = Counter(
    "retention_rows_deleted_total",
    "source_fetch_log rows deleted by the retention job.",
    registry=REGISTRY,
)
MONITORING_RUN_DURATION_SECONDS = Histogram(
    "monitoring_run_duration_seconds",
    "Wall-clock duration of a monitoring run.",
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1800, 3600),
    registry=REGISTRY,
)

router = APIRouter()


@router.get("/metrics")
def metrics(_current_user: User = Depends(get_current_user)) -> Response:
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
