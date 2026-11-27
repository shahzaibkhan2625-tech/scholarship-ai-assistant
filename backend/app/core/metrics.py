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

The 003 budget series are different: `BudgetCollector` reads the ledger, pause and check-state tables at
scrape time (FR-OBS-1), so they survive restarts and agree across workers. A failing read drops those series
for that scrape and never breaks `/metrics`.
"""

import logging
from collections.abc import Callable, Iterator
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily

from app.api.deps import get_current_user
from app.models.user import User

logger = logging.getLogger(__name__)

__all__ = [
    "BUDGET_COLLECTOR",
    "BudgetCollector",
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


class BudgetCollector:
    """Scrape-time collector for the 003 budget series (`contracts/api-delta.md` section 4)."""

    def __init__(self, session_factory: Callable | None = None, clock: Callable[[], datetime] | None = None) -> None:
        self.session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def describe(self) -> list:
        return []  # dynamic series: registration must not read the database

    def collect(self) -> Iterator[GaugeMetricFamily]:
        from app.services import llm_budget

        try:
            factory = self.session_factory
            if factory is None:
                from app.data.repositories.db import SessionLocal as factory
            now = self._clock()
            with factory() as db:
                snapshot = llm_budget.budget_snapshot(db, now=now)
                sources = llm_budget.source_cadence_rows(db, now=now)
        except Exception as exc:  # noqa: BLE001 - a scrape must survive a database hiccup
            logger.warning("budget metrics unavailable (%s)", type(exc).__name__)
            return
        allowance = GaugeMetricFamily("llm_budget_allowance", "Configured daily allowance.", labels=["request_type"])
        used = GaugeMetricFamily("llm_budget_used", "Requests allowed today.", labels=["request_type", "priority_class"])
        remaining = GaugeMetricFamily("llm_budget_remaining", "Allowance minus used today.", labels=["request_type"])
        reserve = GaugeMetricFamily("llm_budget_reserve", "User-facing reserve.", labels=["request_type"])
        events = GaugeMetricFamily(
            "llm_budget_events", "Today's budget events.", labels=["request_type", "priority_class", "event"]
        )
        paused = GaugeMetricFamily(
            "llm_provider_paused_until_timestamp", "Unix time the provider pause ends, 0 if none.", labels=["request_type"]
        )
        for request_type, view in snapshot.request_types.items():
            if view.allowance is not None:
                allowance.add_metric([request_type], view.allowance)
                remaining.add_metric([request_type], view.remaining)
            if view.reserve is not None:
                reserve.add_metric([request_type], view.reserve)
            for priority_class, count in view.used.items():
                used.add_metric([request_type, priority_class], count)
            for (priority_class, event), count in view.events_by_class.items():
                events.add_metric([request_type, priority_class, event], count)
            paused.add_metric([request_type], view.paused_until.timestamp() if view.paused_until else 0)
        reset = GaugeMetricFamily("llm_budget_seconds_to_reset", "Seconds to the next budget-day boundary.")
        reset.add_metric([], snapshot.seconds_to_reset)
        pending = GaugeMetricFamily("classify_pending_size", "Records waiting for a deferred classification.")
        pending.add_metric([], snapshot.classify_pending)
        noisy = GaugeMetricFamily(
            "source_check_noisy", "1 if the source's fingerprint changes at almost every check.", labels=["domain"]
        )
        for row in sources:
            noisy.add_metric([row.domain], 1 if row.noisy else 0)
        yield from (allowance, used, remaining, reserve, events, reset, paused, pending, noisy)


BUDGET_COLLECTOR = BudgetCollector()
REGISTRY.register(BUDGET_COLLECTOR)

router = APIRouter()


@router.get("/metrics")
def metrics(_current_user: User = Depends(get_current_user)) -> Response:
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
