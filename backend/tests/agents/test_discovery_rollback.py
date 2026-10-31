"""010 US7 T417 (FR-RECOV-1/2, SC-007): a database error inside one discovery step is skipped, the
remaining steps run, the next write succeeds and the closing coverage summary returns, so
`POST /discovery` answers 200 instead of failing on an aborted transaction.

Started at the route. The fault is a statement that fails inside Postgres (`SELECT 1/0`), which
really aborts the transaction. Real (rolled-back) DB."""

import uuid
from functools import partial

import pytest
from sqlalchemy import text

from app.agents.discovery import agent as discovery_agent
from app.data.repositories import source_repo
from app.models.source import FetchStatus
from app.schemas.source import SourceFetchLogCreate, SourceRegistryCreate
from app.services.profile import get_or_create_profile
from app.tools.api_connector import ApiConnectorOutput


def test_database_error_in_one_discovery_step_is_skipped_and_the_route_still_answers(
    client, db_session_factory, make_user, monkeypatch
):
    session = db_session_factory()
    country = f"Rollbackland-{uuid.uuid4().hex[:6]}"
    sources = [
        source_repo.upsert_source_by_domain(
            session,
            SourceRegistryCreate(
                name=f"Discovery Rollback {label}", source_type="api", official_status="official",
                domain=f"disc-{label}-{uuid.uuid4().hex[:8]}.example.com", access_method="api",
                reliability_level="high", status="active", discovery_role=True, country=country,
            ),
        )
        for label in ("a", "b")
    ]
    broken, healthy = sources
    seen: list[uuid.UUID] = []

    def fetch_api(db, source_id, query):
        seen.append(source_id)
        if source_id == broken.id:
            db.execute(text("SELECT 1/0"))  # a database error inside the step
        source_repo.log_fetch(db, SourceFetchLogCreate(source_id=source_id, status=FetchStatus.OK, retry_count=0))
        return ApiConnectorOutput(source_id=source_id, query=query, status="ok", records=[])

    real_run_discovery = discovery_agent.run_discovery
    results = []

    def run_discovery(db, profile):
        result = real_run_discovery(db, profile, fetch_api=fetch_api)
        results.append(result)
        return result

    monkeypatch.setattr("app.api.discovery.run_discovery", run_discovery)
    student = make_user()
    # precondition seeded in the database so the route is the first call under test
    with db_session_factory() as seed:
        get_or_create_profile(seed, student["id"]).target_countries = [country]
        seed.commit()

    response = client.post("/discovery", headers=student["headers"])

    assert response.status_code == 200
    assert set(seen) == {broken.id, healthy.id}  # the step after the failed one still ran
    session.expire_all()
    assert [log.status for log in source_repo.get_fetch_logs_for_source(session, healthy.id)] == [FetchStatus.OK]
    assert any(str(broken.id) in error for error in results[0].fetch_errors)  # the failure is reported, not hidden
    assert response.json()["coverage"] is not None
    session.close()
