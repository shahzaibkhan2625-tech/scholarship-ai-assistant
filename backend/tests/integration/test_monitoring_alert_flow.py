"""SC-001 end-to-end (002 US2, T187/T196): content change -> monitoring run ->
record updated -> owner alerted, with fixtures only (fake channels, no SMTP,
no network, no LLM)."""

from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.data.repositories import alert_repo, scholarship_repo
from app.models.alert import Alert
from app.models.match import Match
from app.models.monitoring import MonitoringStatus
from app.models.scholarship import LifecycleStatus, ScholarshipField
from tests.alert_helpers import FUTURE, FUTURE_2, PAST, FakeChannel, build_monitor_env, cand


@pytest.fixture
def env(db_session_factory, monkeypatch):
    e = build_monitor_env(db_session_factory, monkeypatch)
    e.email, e.in_app = FakeChannel("email"), FakeChannel("in_app")
    e.channels = {"in_app": e.in_app, "email": e.email}
    try:
        yield e
    finally:
        e.session.close()


def _alerts(env, owner):
    stmt = select(Alert).where(Alert.user_id == owner.id).order_by(Alert.created_at)
    return list(env.session.execute(stmt).scalars().all())


def _run(env):
    """One monitoring run. Matches get `created_at = now()` (the transaction start), and this
    fixture keeps everything in one transaction, so every match would tie. Age the existing
    matches first so each run's match is strictly the latest; the alert logic's "prior match"
    is then unambiguous instead of relying on the tiebreaker."""
    env.session.execute(update(Match).values(created_at=Match.created_at - timedelta(minutes=1)))
    env.session.commit()
    return env.run(alert_channels=env.channels)


def test_full_catch_it_and_tell_me_loop(env):
    owner = env.add_owner()
    source = env.make_source()
    alpha, beta = env.name("Alpha"), env.name("Beta")

    # Baseline: Alpha is stored with a deadline that has already passed. Alerts are
    # switched off for this run, but re-matching still happens (FR-ALERT-5), which
    # gives the owner a prior (failing) match for Alpha.
    alert_repo.upsert_preferences(env.session, owner.id, in_app_enabled=False, email_enabled=False)
    env.listing.by_host[source.domain] = [cand(alpha, deadline=PAST)]
    _run(env)
    assert _alerts(env, owner) == []
    alert_repo.upsert_preferences(env.session, owner.id, in_app_enabled=True, email_enabled=True)

    # One run: Alpha's deadline moves into the future (changed -> EXPIRED lifts to UPDATED) and
    # Beta appears with an already-passed deadline (new, expired on arrival -> no alert).
    env.listing.by_host[source.domain] = [cand(alpha, deadline=FUTURE), cand(beta, deadline=PAST)]
    run = _run(env)

    assert run.status == MonitoringStatus.COMPLETED and run.changes_detected == 2
    stored = {s.name: s for s in scholarship_repo.list_for_source(env.session, source.id)}
    assert stored[alpha].lifecycle_status == LifecycleStatus.UPDATED
    assert stored[beta].lifecycle_status == LifecycleStatus.EXPIRED
    prior = env.session.execute(
        select(ScholarshipField).where(
            ScholarshipField.scholarship_id == stored[alpha].id, ScholarshipField.key == "_prior:deadline"
        )
    ).scalar_one()
    assert prior.value["value"] == PAST  # the prior deadline is still auditable

    (alert,) = _alerts(env, owner)
    assert alert.scholarship_id == stored[alpha].id
    assert alert.monitoring_run_id == run.id
    assert alert.change_summary["kind"] == "eligibility_changed"
    assert alert.channels_requested == ["in_app", "email"]
    assert alert.delivered_in_app_at is not None and alert.delivered_email_at is not None
    assert len(env.email.calls) == 1

    # Email off -> the next qualifying change is in-app only and sends no email.
    alert_repo.upsert_preferences(env.session, owner.id, email_enabled=False)
    env.listing.by_host[source.domain] = [cand(alpha, deadline=PAST), cand(beta, deadline=PAST)]
    _run(env)
    latest = _alerts(env, owner)[-1]
    assert latest.channels_requested == ["in_app"] and latest.change_summary["kind"] == "eligibility_changed"
    assert len(env.email.calls) == 1

    # A change that affects nobody's eligibility creates no alert (same hard-constraint set).
    count = len(_alerts(env, owner))
    env.listing.by_host[source.domain] = [cand(alpha, deadline=PAST, funding_status="partially_funded"), cand(beta, deadline=PAST)]
    _run(env)
    assert len(_alerts(env, owner)) == count


def test_deadline_moving_further_into_the_past_keeps_expired_and_does_not_alert(env):
    owner = env.add_owner()
    source = env.make_source()
    alpha = env.name("Alpha")

    env.listing.by_host[source.domain] = [cand(alpha, deadline=PAST)]
    env.run(alert_channels=env.channels)

    # Still EXPIRED after the deadline changes to another past date: not lifted to UPDATED, no alert.
    env.listing.by_host[source.domain] = [cand(alpha, deadline="2019-01-01")]
    run = env.run(alert_channels=env.channels)

    assert run.status == MonitoringStatus.COMPLETED
    (stored,) = scholarship_repo.list_for_source(env.session, source.id)
    assert stored.lifecycle_status == LifecycleStatus.EXPIRED
    assert _alerts(env, owner) == []
    assert env.email.calls == []


def test_failing_email_channel_records_error_without_failing_run_or_rolling_back(env):
    owner = env.add_owner()
    source = env.make_source()
    alpha = env.name("Alpha")
    env.email.ok, env.email.error = False, "smtp down"
    env.listing.by_host[source.domain] = [cand(alpha, deadline=FUTURE_2)]

    run = env.run(alert_channels=env.channels)

    assert run.status == MonitoringStatus.COMPLETED
    assert len(scholarship_repo.find_by_name(env.session, alpha)) == 1
    (alert,) = _alerts(env, owner)
    assert alert.email_error == "smtp down" and alert.delivered_email_at is None
    assert alert.delivered_in_app_at is not None
