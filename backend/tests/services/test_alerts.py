"""`services/alerts.py` tests (002 US2, T184; ADR-0007).

Real (rolled-back) DB. Owner selection is scoped to test-created users and the
soft-preference LLM call is stubbed, so nothing here reaches a real profile,
Gemini, or SMTP.
"""

import ast
import pathlib
import smtplib
import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.agents.matching.agent import build_verdict
from app.data.repositories import alert_repo, match_repo, monitoring_repo
from app.models.alert import Alert
from app.models.match import Match
from app.models.monitoring import MonitoringTrigger
from app.schemas.match import CriterionOutcome, CriterionResult
from app.services import alerts
from app.services.alerts import ChangeEvent, EmailChannel, InAppChannel, RematchBudget
from tests.alert_helpers import FUTURE, FUTURE_2, PAST, FakeChannel, make_profile, make_scholarship, make_user


@pytest.fixture
def svc(db_session_factory, monkeypatch):
    session = db_session_factory()
    users: list = []
    monkeypatch.setattr("app.services.matching.produce_soft_preference_outcomes", lambda *a, **k: ([], []))
    monkeypatch.setattr(alert_repo, "profile_user_ids", lambda db: list(users))

    class S:
        pass

    s = S()
    s.session, s.users = session, users
    s.run = monitoring_repo.create_run(session, MonitoringTrigger.MANUAL)
    s.in_app, s.email = FakeChannel("in_app"), FakeChannel("email")
    s.channels = {"in_app": s.in_app, "email": s.email}

    def owner():
        user = make_user(session)
        make_profile(session, user.id)
        users.append(user.id)
        return user

    def process(events, **kwargs):
        kwargs.setdefault("budget", RematchBudget(500))
        kwargs.setdefault("channels", s.channels)
        return alerts.process_changes(session, s.run.id, events, **kwargs)

    def rows(user, scholarship=None):
        stmt = select(Alert).where(Alert.user_id == user.id)
        if scholarship is not None:
            stmt = stmt.where(Alert.scholarship_id == scholarship.id)
        return list(session.execute(stmt).scalars().all())

    s.owner, s.process, s.rows = owner, process, rows
    yield s
    session.close()


def _new(scholarship):
    return ChangeEvent(scholarship_id=scholarship.id, kind="new", fields=())


def _updated(scholarship, *fields):
    return ChangeEvent(scholarship_id=scholarship.id, kind="updated", fields=tuple(fields))


def _verdict(*hard):
    return build_verdict(uuid.uuid4(), [CriterionOutcome(criterion=c, result=CriterionResult(r), evidence="test evidence") for c, r in hard], [], [], [])


# --- decision rule (D1 / D3, pure) ----------------------------------------------


def test_new_scholarship_alerts_only_on_favorable_verdicts():
    likely = _verdict(("deadline", "pass"))
    assert alerts.decide_alert(ChangeEvent(None, "new", ()), None, likely)["kind"] == "new_match"
    assert alerts.decide_alert(ChangeEvent(None, "new", ()), None, _verdict(("deadline", "unknown"))) is None
    assert alerts.decide_alert(ChangeEvent(None, "new", ()), None, _verdict(("deadline", "fail"))) is None


def test_prior_match_alerts_on_hard_constraint_set_change_only():
    prior = SimpleNamespace(
        eligibility_verdict="not", hard_constraints=[{"criterion": "deadline", "result": "fail", "evidence": "old"}]
    )
    changed = alerts.decide_alert(ChangeEvent(None, "updated", ("deadline",)), prior, _verdict(("deadline", "pass")))
    assert changed["kind"] == "eligibility_changed"
    assert changed["fields"] == ["deadline"]
    assert changed["previous_verdict"] == "not" and changed["verdict"] == "likely"

    # Same hard-constraint set (evidence text differs) but a different verdict (soft-only drift): no alert.
    same_set = SimpleNamespace(
        eligibility_verdict="possibly", hard_constraints=[{"criterion": "deadline", "result": "pass", "evidence": "x"}]
    )
    assert alerts.decide_alert(ChangeEvent(None, "updated", ("funding_status",)), same_set, _verdict(("deadline", "pass"))) is None


def test_changed_event_without_baseline_uses_favorable_rule_as_details_changed():
    summary = alerts.decide_alert(ChangeEvent(None, "updated", ("deadline",)), None, _verdict(("deadline", "pass")))
    assert summary["kind"] == "details_changed"
    assert alerts.decide_alert(ChangeEvent(None, "updated", ("deadline",)), None, _verdict(("deadline", "fail"))) is None


# --- generation (DB) --------------------------------------------------------------


def test_new_scholarship_creates_one_alert_for_each_eligible_owner(svc):
    owner, other = svc.owner(), svc.owner()
    scholarship = make_scholarship(svc.session, name="Fresh Grant")

    summary = svc.process([_new(scholarship)])

    for user in (owner, other):
        (alert,) = svc.rows(user, scholarship)
        latest = match_repo.latest_for_user_and_scholarship(svc.session, user.id, scholarship.id)
        assert alert.match_id == latest.id
        assert alert.monitoring_run_id == svc.run.id
        assert alert.channels_requested == ["in_app", "email"]
        assert alert.change_summary["kind"] == "new_match"
        assert alert.change_summary["scholarship_name"] == "Fresh Grant"
        assert alert.delivered_in_app_at is not None and alert.delivered_email_at is not None
    assert summary.alerts_created == 2


def test_non_impacting_change_creates_no_alert_rows(svc):
    owner = svc.owner()
    past = make_scholarship(svc.session, deadline=PAST)
    unknown = make_scholarship(svc.session, deadline=None)

    summary = svc.process([_new(past), _new(unknown)])

    assert svc.rows(owner) == []
    assert summary.alerts_created == 0
    assert svc.in_app.calls == [] and svc.email.calls == []


def test_changed_scholarship_only_rematches_owners_with_prior_match_or_application(svc):
    from app.services.matching import match_scholarship
    from app.data.repositories import application_repo, profile_repo

    matched, applicant, stranger = svc.owner(), svc.owner(), svc.owner()
    scholarship = make_scholarship(svc.session, deadline=FUTURE)
    match_scholarship(svc.session, matched.id, profile_repo.get_by_user_id(svc.session, matched.id), scholarship)
    application_repo.create(svc.session, applicant.id, scholarship.id)
    scholarship.deadline = date.fromisoformat(PAST)
    svc.session.commit()

    svc.process([_updated(scholarship, "deadline")])

    (alert,) = svc.rows(matched, scholarship)
    assert alert.change_summary["kind"] == "eligibility_changed"
    assert alert.change_summary["fields"] == ["deadline"]
    assert svc.rows(applicant) == []  # no baseline + now failing deadline: not favorable
    assert match_repo.latest_for_user_and_scholarship(svc.session, applicant.id, scholarship.id) is not None
    assert match_repo.latest_for_user_and_scholarship(svc.session, stranger.id, scholarship.id) is None
    assert svc.rows(stranger) == []


def test_changed_scholarship_with_unchanged_hard_constraints_does_not_alert(svc):
    from app.data.repositories import profile_repo
    from app.services.matching import match_scholarship

    owner = svc.owner()
    scholarship = make_scholarship(svc.session, deadline=FUTURE)
    match_scholarship(svc.session, owner.id, profile_repo.get_by_user_id(svc.session, owner.id), scholarship)
    scholarship.deadline = date.fromisoformat(FUTURE_2)  # still passes
    svc.session.commit()

    svc.process([_updated(scholarship, "deadline")])

    assert svc.rows(owner) == []


# --- preferences / channels -------------------------------------------------------


def test_channels_requested_snapshots_current_preferences(svc):
    owner = svc.owner()
    alert_repo.upsert_preferences(svc.session, owner.id, email_enabled=False)
    scholarship = make_scholarship(svc.session)

    svc.process([_new(scholarship)])

    (alert,) = svc.rows(owner, scholarship)
    assert alert.channels_requested == ["in_app"]
    assert alert.delivered_email_at is None
    assert svc.email.calls == [] and len(svc.in_app.calls) == 1


def test_both_channels_off_means_no_alert_and_no_send_but_rematch_still_runs(svc):
    owner = svc.owner()
    alert_repo.upsert_preferences(svc.session, owner.id, in_app_enabled=False, email_enabled=False)
    scholarship = make_scholarship(svc.session)

    svc.process([_new(scholarship)])

    assert svc.rows(owner) == []
    assert svc.in_app.calls == [] and svc.email.calls == []
    assert match_repo.latest_for_user_and_scholarship(svc.session, owner.id, scholarship.id) is not None


def test_a_third_channel_can_be_registered_without_touching_generation(svc):
    owner = svc.owner()
    webhook = FakeChannel("webhook")
    scholarship = make_scholarship(svc.session)

    svc.process([_new(scholarship)], channels={**svc.channels, "webhook": webhook})

    (alert,) = svc.rows(owner, scholarship)
    assert alert.channels_requested == ["in_app", "email", "webhook"]
    assert len(webhook.calls) == 1


# --- email failure / retry ----------------------------------------------------------


def _alert_for(svc, owner, scholarship):
    return svc.rows(owner, scholarship)[0]


def test_unset_smtp_host_records_explicit_email_error_and_never_raises(svc, monkeypatch):
    monkeypatch.setattr(alerts, "settings", SimpleNamespace(smtp_host=None, alerts_from_email="a@example.com"))
    owner = svc.owner()
    scholarship = make_scholarship(svc.session)

    svc.process([_new(scholarship)], channels={"in_app": InAppChannel(), "email": EmailChannel()})

    alert = _alert_for(svc, owner, scholarship)
    assert "SMTP_HOST" in alert.email_error
    assert alert.delivered_email_at is None
    assert alert.delivered_in_app_at is not None


def test_email_failure_is_stored_and_retry_updates_the_same_row(svc, monkeypatch):
    settings = SimpleNamespace(
        smtp_host="smtp.example.test", smtp_port=587, smtp_username=None, smtp_password=None,
        alerts_from_email="alerts@example.test",
    )
    monkeypatch.setattr(alerts, "settings", settings)
    sent = []

    class _SMTP:
        fail = True

        def __init__(self, host, port, timeout=None):
            if _SMTP.fail:
                raise smtplib.SMTPConnectError(421, "nope")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, *a):
            pass

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr(smtplib, "SMTP", _SMTP)
    owner = svc.owner()
    scholarship = make_scholarship(svc.session, name="Retry Grant")
    channels = {"in_app": InAppChannel(), "email": EmailChannel()}

    svc.process([_new(scholarship)], channels=channels)
    alert = _alert_for(svc, owner, scholarship)
    assert alert.email_error and alert.delivered_email_at is None

    _SMTP.fail = False
    context = alerts.AlertContext(
        recipient_email=owner.email, scholarship_id=scholarship.id, scholarship_name="Retry Grant"
    )
    alerts.dispatch_alert(svc.session, alert, context, channels)

    svc.session.expire_all()
    rows = svc.rows(owner, scholarship)
    assert len(rows) == 1 and rows[0].id == alert.id
    assert rows[0].delivered_email_at is not None and rows[0].email_error is None
    body = sent[0].get_content()
    assert "Retry Grant" in body and str(scholarship.id) in body and "http" not in body


# --- idempotency / cap ---------------------------------------------------------------


def test_same_run_event_twice_creates_one_alert(svc):
    owner = svc.owner()
    scholarship = make_scholarship(svc.session)

    svc.process([_new(scholarship)])
    second = svc.process([_new(scholarship)])

    assert len(svc.rows(owner, scholarship)) == 1
    assert second.skipped_duplicates == 1 and second.alerts_created == 0


def test_rematch_cap_stops_generation_without_raising(svc, caplog):
    first, second = svc.owner(), svc.owner()
    scholarship = make_scholarship(svc.session)

    with caplog.at_level("WARNING"):
        summary = svc.process([_new(scholarship)], budget=RematchBudget(1))

    assert summary.cap_hit is True
    assert summary.alerts_created == 1
    assert len(svc.rows(first)) + len(svc.rows(second)) == 1
    assert any("cap" in record.message.lower() for record in caplog.records)


def test_per_owner_failure_is_isolated_and_logged(svc, monkeypatch):
    first, second = svc.owner(), svc.owner()
    scholarship = make_scholarship(svc.session)
    real = alerts.match_scholarship

    def flaky(db, user_id, profile, sch):
        if user_id == first.id:
            raise RuntimeError("match exploded")
        return real(db, user_id, profile, sch)

    monkeypatch.setattr(alerts, "match_scholarship", flaky)

    summary = svc.process([_new(scholarship)])

    assert summary.errors == 1
    assert svc.rows(first) == [] and len(svc.rows(second)) == 1


# --- structure ------------------------------------------------------------------------


def test_alerts_service_imports_no_llm_modules():
    source = (pathlib.Path(alerts.__file__)).read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = ("app.core.llm", "langchain", "google", "app.tools")
    assert not [m for m in imported if m.startswith(forbidden)], imported


def test_rematch_cap_setting_defaults_to_500(monkeypatch):
    from app.core.config import Settings

    monkeypatch.delenv("MAX_ALERT_REMATCHES_PER_RUN", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@localhost/db")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    assert Settings(_env_file=None).max_alert_rematches_per_run == 500
    monkeypatch.setenv("MAX_ALERT_REMATCHES_PER_RUN", "7")
    assert Settings(_env_file=None).max_alert_rematches_per_run == 7
