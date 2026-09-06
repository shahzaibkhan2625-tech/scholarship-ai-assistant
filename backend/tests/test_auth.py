import uuid

import pytest
from fastapi.testclient import TestClient


def _get_client_and_session():
    try:
        from app.data.repositories.db import SessionLocal
    except RuntimeError:
        pytest.skip("DATABASE_URL is not configured; skipping auth tests.")

    from app.main import app

    return TestClient(app), SessionLocal


@pytest.fixture
def client_and_cleanup():
    client, SessionLocal = _get_client_and_session()
    created_emails: list[str] = []

    yield client, created_emails

    if created_emails:
        from app.models.user import User

        with SessionLocal() as session:
            session.query(User).filter(User.email.in_(created_emails)).delete(
                synchronize_session=False
            )
            session.commit()


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


def test_signup_success(client_and_cleanup) -> None:
    client, created_emails = client_and_cleanup
    email = _unique_email()
    created_emails.append(email)

    response = client.post("/auth/signup", json={"email": email, "password": "s3cret-pass"})

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == email
    assert "id" in body
    assert "created_at" in body
    assert "password" not in body
    assert "password_hash" not in body


def test_signup_duplicate_email_rejected(client_and_cleanup) -> None:
    client, created_emails = client_and_cleanup
    email = _unique_email()
    created_emails.append(email)

    first = client.post("/auth/signup", json={"email": email, "password": "s3cret-pass"})
    assert first.status_code == 201

    second = client.post("/auth/signup", json={"email": email, "password": "another-pass"})
    assert second.status_code == 409


def test_login_success_returns_token(client_and_cleanup) -> None:
    client, created_emails = client_and_cleanup
    email = _unique_email()
    created_emails.append(email)
    password = "s3cret-pass"

    signup = client.post("/auth/signup", json={"email": email, "password": password})
    assert signup.status_code == 201

    login = client.post("/auth/login", json={"email": email, "password": password})

    assert login.status_code == 200
    body = login.json()
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str) and body["access_token"]


def test_login_wrong_password_returns_401(client_and_cleanup) -> None:
    client, created_emails = client_and_cleanup
    email = _unique_email()
    created_emails.append(email)

    signup = client.post("/auth/signup", json={"email": email, "password": "correct-pass"})
    assert signup.status_code == 201

    login = client.post("/auth/login", json={"email": email, "password": "wrong-pass"})

    assert login.status_code == 401


def test_logout_returns_200(client_and_cleanup) -> None:
    client, _ = client_and_cleanup

    response = client.post("/auth/logout")

    assert response.status_code == 200
