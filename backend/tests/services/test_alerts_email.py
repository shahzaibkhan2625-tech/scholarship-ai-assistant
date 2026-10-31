"""010 US8 T424 (FR-MAIL-1/2, SC-008): Gmail alert mail, started at the alert dispatch of a manual
monitoring run. A recording fake replaces `smtplib.SMTP`: no real mail server, no network, no secret.

Configured -> one message to the user's address over a STARTTLS context that verifies the server
certificate and host name; the app password is sent without the spaces Google prints it with.
Unconfigured / no recipient / send failure -> the in-app alert still exists, `email_error` holds a
short code (never raw server text) and one WARNING line gives the reason without any secret.
Real (rolled-back) DB."""

import logging
import smtplib
import ssl
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.alert import Alert
from app.models.monitoring import MonitoringStatus
from app.services import alerts
from tests.alert_helpers import FUTURE_2, build_monitor_env, cand

APP_PASSWORD_AS_SHOWN = "abcd efgh ijkl mnop"  # allow-secret: fake, shape only
APP_PASSWORD = "abcdefghijklmnop"  # allow-secret: fake, shape only
SERVER_TEXT = f"5.7.8 Username and Password not accepted for {APP_PASSWORD}"  # allow-secret: fake


class FakeSMTP:
    """Records every call. `fail_at` makes one step raise like Gmail would."""

    instances: list["FakeSMTP"] = []
    fail_at: str | None = None

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.starttls_context = None
        self.login_args = None
        self.messages = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.starttls_context = context

    def login(self, user, password):
        self.login_args = (user, password)
        if FakeSMTP.fail_at == "login":
            raise smtplib.SMTPAuthenticationError(535, SERVER_TEXT.encode())

    def send_message(self, message):
        self.messages.append(message)


def _settings(**overrides):
    base = dict(
        smtp_host="smtp.gmail.com", smtp_port=587, smtp_username="alerts@example.test",
        smtp_password=APP_PASSWORD_AS_SHOWN, alerts_from_email="alerts@example.test",
    )
    return SimpleNamespace(**{**base, **overrides})


@pytest.fixture
def env(db_session_factory, monkeypatch):
    e = build_monitor_env(db_session_factory, monkeypatch)
    FakeSMTP.instances, FakeSMTP.fail_at = [], None
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(alerts, "settings", _settings())
    e.source = e.make_source()
    e.listing.by_host[e.source.domain] = [cand(e.name("Alpha"), deadline=FUTURE_2)]
    try:
        yield e
    finally:
        e.session.close()


def _alerts(env, owner):
    env.session.expire_all()
    return list(env.session.execute(select(Alert).where(Alert.user_id == owner.id)).scalars().all())


def _mail_warnings(caplog):
    return [r for r in caplog.records if r.name == "app.services.alerts" and r.levelno == logging.WARNING]


def test_configured_sends_one_message_over_verified_starttls(env):
    owner = env.add_owner()

    run = env.run()

    assert run.status == MonitoringStatus.COMPLETED
    (alert,) = _alerts(env, owner)
    assert alert.delivered_in_app_at is not None and alert.delivered_email_at is not None
    assert alert.email_error is None
    (smtp,) = FakeSMTP.instances
    assert (smtp.host, smtp.port) == ("smtp.gmail.com", 587)
    assert [m["To"] for m in smtp.messages] == [owner.email]
    assert smtp.messages[0]["From"] == "alerts@example.test"
    assert isinstance(smtp.starttls_context, ssl.SSLContext)
    assert smtp.starttls_context.verify_mode == ssl.CERT_REQUIRED
    assert smtp.starttls_context.check_hostname is True


def test_app_password_is_sent_without_the_spaces_google_prints(env):
    env.add_owner()

    env.run()

    (smtp,) = FakeSMTP.instances
    assert smtp.login_args == ("alerts@example.test", APP_PASSWORD)


@pytest.mark.parametrize("missing", [{"smtp_host": None}, {"alerts_from_email": None}])
def test_unconfigured_keeps_the_in_app_alert_and_logs_the_reason(env, monkeypatch, caplog, missing):
    monkeypatch.setattr(alerts, "settings", _settings(**missing))
    owner = env.add_owner()

    with caplog.at_level(logging.WARNING):
        run = env.run()

    assert run.status == MonitoringStatus.COMPLETED
    (alert,) = _alerts(env, owner)
    assert alert.delivered_in_app_at is not None
    assert alert.email_error == "not_configured" and alert.delivered_email_at is None
    (line,) = _mail_warnings(caplog)
    assert "not_configured" in line.getMessage()
    assert FakeSMTP.instances == []


def test_send_failure_keeps_the_alert_gives_a_short_code_and_leaks_nothing(env, caplog):
    FakeSMTP.fail_at = "login"
    owner = env.add_owner()

    with caplog.at_level(logging.WARNING):
        run = env.run()

    assert run.status == MonitoringStatus.COMPLETED
    (alert,) = _alerts(env, owner)
    assert alert.delivered_in_app_at is not None
    assert alert.email_error == "send_failed" and alert.delivered_email_at is None
    (line,) = _mail_warnings(caplog)
    message = line.getMessage()
    assert "send_failed" in message and "SMTPAuthenticationError" in message and "535" in message
    for leaked in (APP_PASSWORD, APP_PASSWORD_AS_SHOWN, "not accepted", owner.email):
        assert leaked not in message
        assert leaked not in caplog.text


def test_a_user_without_an_address_gets_no_recipient(env, caplog):
    owner = env.add_owner()
    owner.email = ""
    env.session.commit()

    with caplog.at_level(logging.WARNING):
        run = env.run()

    assert run.status == MonitoringStatus.COMPLETED
    (alert,) = _alerts(env, owner)
    assert alert.delivered_in_app_at is not None
    assert alert.email_error == "no_recipient" and alert.delivered_email_at is None
    (line,) = _mail_warnings(caplog)
    assert "no_recipient" in line.getMessage()
    assert FakeSMTP.instances == []
