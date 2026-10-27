"""Shared-database guard: DB-dependent tests must not reach a remote database by default.

The suite creates and deletes rows, so running it against a shared database (e.g. the
Neon instance in .env) is opt-in via ALLOW_SHARED_DB=1. CI uses a localhost Postgres
service and is unaffected."""

import os
from collections.abc import Mapping

import pytest
from sqlalchemy.engine import make_url

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
SKIP_MESSAGE = "DATABASE_URL points to a shared database; set ALLOW_SHARED_DB=1 to run DB tests against it"


def shared_db_skip_reason(database_url: str | None, environ: Mapping[str, str] | None = None) -> str | None:
    """The skip message when `database_url` is a non-local host and ALLOW_SHARED_DB != 1, else None."""
    if not database_url:
        return None  # nothing configured: the callers' own "not configured" skip applies
    env = os.environ if environ is None else environ
    if env.get("ALLOW_SHARED_DB") == "1":
        return None
    host = make_url(database_url).host
    if host is None or host.lower() in LOCAL_HOSTS:  # no host = local unix socket
        return None
    return SKIP_MESSAGE


def _configured_database_url() -> str | None:
    try:
        from app.core.config import settings
    except RuntimeError:  # DATABASE_URL not configured
        return None
    return settings.database_url


def require_db_allowed() -> None:
    """Skip the calling test unless its database is local or ALLOW_SHARED_DB=1. Call before any connection."""
    reason = shared_db_skip_reason(_configured_database_url())
    if reason:
        pytest.skip(reason)
