"""Shared test fixtures. Tests in this suite run against the real (free-tier)
Neon Postgres instance configured in .env — there is no local test DB — so
every DB-touching test skips itself (mirrors tests/test_db_connection.py)
when DATABASE_URL is absent or unreachable, and every fixture that creates
data cleans it up afterward."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError


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
    return _require_db()


@pytest.fixture
def client(db_session_factory):
    from app.main import app

    return TestClient(app)


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
