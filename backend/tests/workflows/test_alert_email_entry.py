"""010 US8 T425 (FR-MAIL-1, SC-008): started at the scheduler tick `_run_scheduled_monitor` and at the
manual run route `POST /monitoring/runs`.

A changed fixture source and a user with email alerts on produce one email through a fake SMTP and
one alert visible through `GET /alerts`. No real mail server, network or LLM. Real (rolled-back) DB."""

import smtplib
import ssl
from functools import partial
from types import SimpleNamespace

from app.services import alerts
from app.tools.web_fetch import FetchResult
from tests.alert_helpers import FUTURE_2, build_monitor_env, cand


class FakeSMTP:
    sent: list = []
    contexts: list = []

    def __init__(self, host, port, timeout=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        FakeSMTP.contexts.append(context)

    def login(self, user, password):
        pass

    def send_message(self, message):
        FakeSMTP.sent.append(message)


def _arrange(db_session_factory, monkeypatch):
    """A changed fixture source, a user with email alerts on, a recording fake SMTP, and the monitor's
    fetch/LLM seams injected into the workflow function the entry point calls."""
    from app.data.repositories import db as db_mod
    from app.workflows.source_monitor import graph as monitor_graph

    env = build_monitor_env(db_session_factory, monkeypatch)
    FakeSMTP.sent, FakeSMTP.contexts = [], []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(
        alerts,
        "settings",
        SimpleNamespace(
            smtp_host="smtp.gmail.com", smtp_port=587, smtp_username="alerts@example.test",
            smtp_password="abcd efgh ijkl mnop", alerts_from_email="alerts@example.test",  # allow-secret: fake
        ),
    )
    owner = env.add_owner()
    source = env.make_source()
    env.listing.by_host[source.domain] = [cand(env.name("Alpha"), deadline=FUTURE_2)]

    # The entry points open their own session and call the real monitor with the real fetch and LLM
    # seams: point the session at the test transaction and inject the fixture seams.
    monkeypatch.setattr(db_mod, "SessionLocal", db_session_factory)
    seamed = partial(
        monitor_graph.run_source_monitor,
        fetch=lambda url: FetchResult(url=url, success=True, status_code=200, html="<html>listing</html>"),
        extract_listing=env.listing,
        sleep=lambda _delay: None,
    )
    monkeypatch.setattr(monitor_graph, "run_source_monitor", seamed)
    monkeypatch.setattr("app.api.monitoring.run_source_monitor", seamed)
    monkeypatch.setattr("app.api.monitoring.SessionLocal", db_session_factory)
    return env, owner


def _assert_one_email_and_one_listed_alert(client, env, owner):
    from app.core.security import create_access_token

    assert [m["To"] for m in FakeSMTP.sent] == [owner.email]
    (context,) = FakeSMTP.contexts
    assert isinstance(context, ssl.SSLContext) and context.verify_mode == ssl.CERT_REQUIRED
    token = create_access_token(str(owner.id))
    response = client.get("/alerts", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    (listed,) = response.json()
    assert listed["email_error"] is None and listed["delivered_email_at"] is not None
    env.session.close()


def test_scheduler_tick_sends_one_email_and_the_alert_is_listed(client, db_session_factory, monkeypatch):
    from app.scheduling import scheduler

    env, owner = _arrange(db_session_factory, monkeypatch)

    scheduler._run_scheduled_monitor()  # first call: the tick

    _assert_one_email_and_one_listed_alert(client, env, owner)


def test_manual_run_route_sends_one_email_and_the_alert_is_listed(client, make_user, db_session_factory, monkeypatch):
    import app.api.monitoring as monitoring_api

    env, owner = _arrange(db_session_factory, monkeypatch)
    operator = make_user(is_operator=True)
    started = []
    real_execute_run = monitoring_api._execute_run
    # run the real background body (its own session, as in production) after the response
    monkeypatch.setattr(monitoring_api, "_execute_run", started.append)

    response = client.post("/monitoring/runs", headers=operator["headers"])  # first call: the route

    assert response.status_code == 202
    (run_id,) = started
    real_execute_run(run_id)
    _assert_one_email_and_one_listed_alert(client, env, owner)
