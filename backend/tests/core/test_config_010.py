"""010 T334: new settings and the migration_url helper."""

import pytest

from app.core.config import Settings, migration_url

_NEW_VARS = ("MAX_UPLOAD_BYTES", "MAX_FETCH_REDIRECTS", "MIGRATION_DATABASE_URL")


@pytest.fixture
def env(monkeypatch):
    for name in _NEW_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:pw@localhost/db")  # allow-secret
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-0123456789abcdef-0123456789")  # allow-secret
    return monkeypatch


def test_defaults(env):
    s = Settings(_env_file=None)
    assert s.max_upload_bytes == 10485760
    assert s.max_fetch_redirects == 5
    assert s.migration_database_url is None


def test_overrides(env):
    env.setenv("MAX_UPLOAD_BYTES", "1024")
    env.setenv("MAX_FETCH_REDIRECTS", "0")
    s = Settings(_env_file=None)
    assert (s.max_upload_bytes, s.max_fetch_redirects) == (1024, 0)


def test_negative_redirects_are_rejected(env):
    env.setenv("MAX_FETCH_REDIRECTS", "-1")
    with pytest.raises(ValueError):
        Settings(_env_file=None)


def test_migration_url_falls_back_to_database_url(env):
    s = Settings(_env_file=None)
    assert migration_url(s) == s.database_url == "postgresql+psycopg://app:pw@localhost/db"  # allow-secret


def test_migration_url_is_used_when_set_and_normalised(env):
    env.setenv("MIGRATION_DATABASE_URL", "postgres://owner:pw@direct.example/db")  # allow-secret
    s = Settings(_env_file=None)
    assert migration_url(s) == "postgresql+psycopg://owner:pw@direct.example/db"  # allow-secret
    assert s.database_url.startswith("postgresql+psycopg://app:")


def test_blank_migration_url_counts_as_unset(env):
    env.setenv("MIGRATION_DATABASE_URL", "  ")
    s = Settings(_env_file=None)
    assert s.migration_database_url is None
    assert migration_url(s) == s.database_url
