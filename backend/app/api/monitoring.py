"""Source-monitoring routes (002 FR-MON-1/5). Bearer auth like every other
router. There is deliberately no way to pass a source list: a run always
covers the governed set `run_source_monitor` loads itself."""

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.data.repositories import monitoring_repo
from app.data.repositories.db import SessionLocal, get_db
from app.models.monitoring import MonitoringTrigger
from app.models.user import User
from app.schemas.monitoring import MonitoringRunDetail, MonitoringRunRead
from app.schemas.source import SourceFetchLogRead
from app.workflows.source_monitor.graph import run_source_monitor

logger = logging.getLogger(__name__)

router = APIRouter()


def _execute_run(run_id: uuid.UUID) -> None:
    """Background body: its own session (the request's is closed by then).
    `run_source_monitor` marks the run failed itself if it raises."""
    with SessionLocal() as session:
        try:
            run_source_monitor(session, trigger=MonitoringTrigger.MANUAL, run_id=run_id)
        except Exception:  # noqa: BLE001
            logger.exception("manual monitoring run %s failed", run_id)


@router.get("/monitoring/runs", response_model=list[MonitoringRunRead])
def list_monitoring_runs(
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MonitoringRunRead]:
    return [MonitoringRunRead.model_validate(run) for run in monitoring_repo.list_runs(db, limit=limit)]


@router.get(
    "/monitoring/runs/{run_id}",
    response_model=MonitoringRunDetail,
    responses={404: {"description": "run_id not found"}},
)
def get_monitoring_run(
    run_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MonitoringRunDetail:
    run = monitoring_repo.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Monitoring run not found")
    detail = MonitoringRunDetail.model_validate(run)
    detail.source_outcomes = [
        SourceFetchLogRead.model_validate(log) for log in monitoring_repo.get_fetch_logs_for_run(db, run_id)
    ]
    return detail


@router.post(
    "/monitoring/runs",
    response_model=MonitoringRunRead,
    status_code=status.HTTP_202_ACCEPTED,
    responses={409: {"description": "a monitoring run is already in progress"}},
)
def trigger_monitoring_run(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MonitoringRunRead:
    run = monitoring_repo.create_run_if_none_running(db, MonitoringTrigger.MANUAL)
    if run is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A monitoring run is already in progress")
    background_tasks.add_task(_execute_run, run.id)
    return MonitoringRunRead.model_validate(run)
