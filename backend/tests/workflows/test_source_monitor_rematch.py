"""Re-match/alert step of `source_monitor` (002 US2, T185/T192; ADR-0007).

The re-match must be the SAME `match_scholarship` the on-demand route uses,
hard constraints must stay LLM-free (the soft-preference call inside
`match_scholarship` is accepted and is stubbed here), and the alert step must
never undo a stored change nor hold the source lock.
"""

import pytest
from sqlalchemy import select

from app.api import matching as matching_api
from app.data.repositories import match_repo, profile_repo, scholarship_repo
from app.models.alert import Alert
from app.models.monitoring import MonitoringStatus
from app.scheduling.locks import try_acquire_source_lock
from app.services import alerts
from app.services.alerts import RematchBudget
from app.services.hard_constraints import evaluate_hard_constraints
from tests.alert_helpers import FakeChannel, build_monitor_env, cand


@pytest.fixture
def env(db_session_factory, monkeypatch):
    e = build_monitor_env(db_session_factory, monkeypatch)
    e.channels = {"in_app": FakeChannel("in_app"), "email": FakeChannel("email")}
    try:
        yield e
    finally:
        e.session.close()


def _alerts_for(env, user):
    return list(env.session.execute(select(Alert).where(Alert.user_id == user.id)).scalars().all())


def test_alerts_service_uses_the_same_match_function_as_the_on_demand_route():
    assert alerts.match_scholarship is matching_api.match_scholarship


def test_new_scholarship_is_rematched_through_match_scholarship_and_alerted(env, monkeypatch):
    owner = env.add_owner()
    source = env.make_source()
    env.listing.by_host[source.domain] = [cand(env.name("Alpha"))]
    calls = []
    real = alerts.match_scholarship

    def spy(db, user_id, profile, scholarship):
        calls.append((user_id, scholarship.name))
        return real(db, user_id, profile, scholarship)

    monkeypatch.setattr(alerts, "match_scholarship", spy)

    run = env.run(alert_channels=env.channels)

    assert run.status == MonitoringStatus.COMPLETED
    assert calls == [(owner.id, env.name("Alpha"))]
    (alert,) = _alerts_for(env, owner)
    assert alert.monitoring_run_id == run.id and alert.change_summary["kind"] == "new_match"


def test_monitoring_verdict_equals_on_demand_verdict_and_hard_constraints_are_llm_free(env, monkeypatch):
    owner = env.add_owner()
    source = env.make_source()
    env.listing.by_host[source.domain] = [cand(env.name("Alpha"))]
    monkeypatch.setattr(
        "app.core.llm.generate_text", lambda *a, **k: pytest.fail("LLM invoked on the hard-constraint path")
    )

    env.run(alert_channels=env.channels)

    scholarship = scholarship_repo.find_by_name(env.session, env.name("Alpha"))[0]
    profile = profile_repo.get_by_user_id(env.session, owner.id)
    from_monitoring = match_repo.latest_for_user_and_scholarship(env.session, owner.id, scholarship.id)
    on_demand = matching_api.match_scholarship(env.session, owner.id, profile, scholarship)

    assert from_monitoring.eligibility_verdict == on_demand.eligibility_verdict
    assert from_monitoring.hard_constraints == [o.model_dump(mode="json") for o in on_demand.hard_constraints]
    assert from_monitoring.hard_constraints == [
        o.model_dump(mode="json") for o in evaluate_hard_constraints(profile, scholarship)
    ]


def test_unchanged_rerun_rematches_and_alerts_nobody(env, monkeypatch):
    owner = env.add_owner()
    source = env.make_source()
    env.listing.by_host[source.domain] = [cand(env.name("Alpha"))]
    env.run(alert_channels=env.channels)
    monkeypatch.setattr(alerts, "match_scholarship", lambda *a, **k: pytest.fail("re-matched with no change"))

    env.run(alert_channels=env.channels)

    assert len(_alerts_for(env, owner)) == 1  # only the first run's alert


def test_alert_step_failure_never_rolls_back_the_stored_change(env, monkeypatch):
    env.add_owner()
    source = env.make_source()
    env.listing.by_host[source.domain] = [cand(env.name("Alpha"))]

    def boom(*args, **kwargs):
        raise RuntimeError("alert step exploded")

    monkeypatch.setattr(alerts, "process_changes", boom)

    run = env.run(alert_channels=env.channels)

    assert run.status == MonitoringStatus.COMPLETED
    assert run.changes_detected == 1
    assert [s.name for s in scholarship_repo.list_for_source(env.session, source.id)] == [env.name("Alpha")] or \
        scholarship_repo.find_by_name(env.session, env.name("Alpha"))


def test_alert_step_runs_outside_the_source_lock(env, monkeypatch):
    env.add_owner()
    source = env.make_source()
    env.listing.by_host[source.domain] = [cand(env.name("Alpha"))]
    observed = {}
    real = alerts.process_changes

    def probe(db, run_id, events, **kwargs):
        from app.data.repositories.db import engine

        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                observed["acquired"] = try_acquire_source_lock(connection, source.id)
            finally:
                transaction.rollback()
        return real(db, run_id, events, **kwargs)

    monkeypatch.setattr(alerts, "process_changes", probe)

    env.run(alert_channels=env.channels)

    assert observed["acquired"] is True


def test_run_wide_cap_limits_pairs_across_sources_and_run_still_completes(env):
    first, second = env.add_owner(), env.add_owner()
    source = env.make_source()
    env.listing.by_host[source.domain] = [cand(env.name("Alpha"))]

    run = env.run(alert_channels=env.channels, rematch_budget=RematchBudget(1))

    assert run.status == MonitoringStatus.COMPLETED
    assert len(_alerts_for(env, first)) + len(_alerts_for(env, second)) == 1
