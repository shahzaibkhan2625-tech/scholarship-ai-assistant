"""The shared-database guard and --offline plumbing; none of these tests needs a database."""

import pytest

from tests import db_guard
from tests.conftest import _is_loopback

REMOTE = "postgresql+psycopg://user:pw@ep-example-123.eu-central-1.aws.neon.tech/db?sslmode=require"  # allow-secret
LOCAL_URLS = [
    "postgresql+psycopg://postgres:postgres@localhost:5432/scholarship_ci",  # allow-secret
    "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/scholarship_ci",  # allow-secret
    "postgresql+psycopg://postgres:postgres@[::1]:5432/scholarship_ci",  # allow-secret
    "postgresql+psycopg:///scholarship_ci",  # unix socket, no host
]


def test_remote_host_is_skipped_by_default():
    assert db_guard.shared_db_skip_reason(REMOTE, {}) == db_guard.SKIP_MESSAGE


def test_skip_message_is_the_documented_one():
    assert db_guard.SKIP_MESSAGE == (
        "DATABASE_URL points to a shared database; set ALLOW_SHARED_DB=1 to run DB tests against it"
    )


@pytest.mark.parametrize("url", LOCAL_URLS)
def test_local_hosts_are_not_skipped(url):
    assert db_guard.shared_db_skip_reason(url, {}) is None


def test_allow_shared_db_opts_in():
    assert db_guard.shared_db_skip_reason(REMOTE, {"ALLOW_SHARED_DB": "1"}) is None


@pytest.mark.parametrize("value", ["", "0", "true", "yes"])
def test_only_exactly_1_opts_in(value):
    assert db_guard.shared_db_skip_reason(REMOTE, {"ALLOW_SHARED_DB": value}) == db_guard.SKIP_MESSAGE


def test_unconfigured_database_is_left_to_the_existing_skip():
    assert db_guard.shared_db_skip_reason(None, {}) is None


def test_require_db_allowed_skips_for_remote_host(monkeypatch):
    monkeypatch.setattr(db_guard, "_configured_database_url", lambda: REMOTE)
    monkeypatch.delenv("ALLOW_SHARED_DB", raising=False)
    with pytest.raises(pytest.skip.Exception, match="ALLOW_SHARED_DB=1"):
        db_guard.require_db_allowed()


def test_require_db_allowed_passes_for_localhost(monkeypatch):
    monkeypatch.setattr(db_guard, "_configured_database_url", lambda: LOCAL_URLS[0])
    monkeypatch.delenv("ALLOW_SHARED_DB", raising=False)
    db_guard.require_db_allowed()


def test_require_db_allowed_passes_for_remote_when_opted_in(monkeypatch):
    monkeypatch.setattr(db_guard, "_configured_database_url", lambda: REMOTE)
    monkeypatch.setenv("ALLOW_SHARED_DB", "1")
    db_guard.require_db_allowed()


def test_require_db_runs_before_any_connection(monkeypatch):
    """conftest._require_db must skip on a remote host without ever importing the engine."""
    import sys

    from tests import conftest

    monkeypatch.setattr(db_guard, "_configured_database_url", lambda: REMOTE)
    monkeypatch.delenv("ALLOW_SHARED_DB", raising=False)
    monkeypatch.setattr(conftest, "require_db_allowed", db_guard.require_db_allowed)
    monkeypatch.setitem(sys.modules, "app.data.repositories.db", None)  # an import would raise ImportError
    with pytest.raises(pytest.skip.Exception, match="shared database"):
        conftest._require_db()


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "LOCALHOST", None])
def test_loopback_hosts_pass_the_offline_block(host):
    assert _is_loopback(host)


@pytest.mark.parametrize("host", ["example.com", "8.8.8.8", "ep-example-123.neon.tech"])
def test_other_hosts_fail_the_offline_block(host):
    assert not _is_loopback(host)
