"""Shared test fixtures. Tests in this suite run against the real (free-tier)
Neon Postgres instance configured in .env — there is no local test DB — so
every DB-touching test skips itself (mirrors tests/test_db_connection.py)
when DATABASE_URL is absent or unreachable, and every fixture that creates
data cleans it up afterward."""

import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker


def find_registered_routes(app, method: str, path: str | re.Pattern) -> list:
    """Locate routes actually registered on `app`, matching against real
    sub-routes rather than `app.routes` entries directly.

    This FastAPI version (0.141.1) wraps each `include_router` call in an
    opaque `_IncludedRouter` on `app.routes` rather than exposing its
    sub-routes' full paths directly — a naive `route.path` scan over
    `app.routes` never matches anything routed through an included router
    (which is how every real route in this app is added), making such a scan
    a false positive/negative regardless of what's actually registered. The
    prefix from `include_context` must be joined with each sub-route's own
    path before comparing.

    `path` is either an exact string (equality match) or a compiled regex
    (`re.fullmatch`) for callers who don't know the future path template's
    parameter name (e.g. asserting a not-yet-implemented dynamic route)."""
    matches = []
    for route in app.routes:
        prefix = getattr(getattr(route, "include_context", None), "prefix", "") or ""
        sub_routes = getattr(getattr(route, "original_router", None), "routes", None) or [route]
        for sub in sub_routes:
            if method not in (getattr(sub, "methods", None) or set()):
                continue
            full_path = f"{prefix}{getattr(sub, 'path', '') or ''}"
            if isinstance(path, re.Pattern):
                if path.fullmatch(full_path):
                    matches.append(sub)
            elif full_path == path:
                matches.append(sub)
    return matches


def _require_db():
    try:
        from app.data.repositories.db import SessionLocal
    except RuntimeError:
        pytest.skip("DATABASE_URL is not configured; skipping DB-dependent tests.")

    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
    except OperationalError:
        pytest.skip("Could not reach the configured database; skipping DB-dependent tests.")

    return SessionLocal


@pytest.fixture
def db_session_factory():
    """A session factory bound to a single connection wrapped in one outer
    transaction for the duration of the test, rolled back at teardown --
    SQLAlchemy's standard "join an external transaction" pattern for test
    isolation -- instead of relying on hand-written, FK-ordered deletes.

    `join_transaction_mode="create_savepoint"` means a `session.commit()`
    made by code under test (including FastAPI endpoints exercised through
    the `client` fixture below, via its `get_db` override) only releases a
    SAVEPOINT and immediately opens a new one; it can never end the outer
    transaction, so the rollback here always discards everything."""
    _require_db()  # skips the test if DATABASE_URL is unset/unreachable
    from app.data.repositories.db import engine

    connection = engine.connect()
    outer_transaction = connection.begin()
    TestingSessionLocal = sessionmaker(bind=connection, autoflush=False, join_transaction_mode="create_savepoint")

    yield TestingSessionLocal

    outer_transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_session_factory):
    from app.data.repositories.db import get_db
    from app.main import app

    def _override_get_db():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _delete_user_cascade(session, user_id: uuid.UUID) -> None:
    from app.models.match import Match
    from app.models.profile import EducationRecord, Experience, MissingInfo, Profile, ProfileCriterion, TestScore
    from app.models.user import User

    profile = session.query(Profile).filter(Profile.user_id == user_id).one_or_none()
    if profile is not None:
        session.query(ProfileCriterion).filter(ProfileCriterion.profile_id == profile.id).delete()
        session.query(MissingInfo).filter(MissingInfo.profile_id == profile.id).delete()
        session.query(EducationRecord).filter(EducationRecord.profile_id == profile.id).delete()
        session.query(TestScore).filter(TestScore.profile_id == profile.id).delete()
        session.query(Experience).filter(Experience.profile_id == profile.id).delete()
        session.delete(profile)

    session.query(Match).filter(Match.user_id == user_id).delete()

    user = session.get(User, user_id)
    if user is not None:
        session.delete(user)

    session.commit()


@pytest.fixture
def authed_user(client, db_session_factory):
    """Signs up + logs in a fresh throwaway user via the real auth endpoints,
    yields a dict with `client`, `token`, `user_id`, `headers`, and tears down
    every row the test created for that user afterward."""
    email = f"test-{uuid.uuid4().hex}@example.com"
    password = "s3cret-pass"  # noqa: S105 - throwaway test fixture credential

    signup = client.post("/auth/signup", json={"email": email, "password": password})
    assert signup.status_code == 201
    user_id = uuid.UUID(signup.json()["id"])

    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    token = login.json()["access_token"]

    yield {
        "client": client,
        "token": token,
        "user_id": user_id,
        "headers": {"Authorization": f"Bearer {token}"},
    }

    with db_session_factory() as session:
        _delete_user_cascade(session, user_id)
