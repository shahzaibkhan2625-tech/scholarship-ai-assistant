"""010 US4: password and email rules on sign-up and sign-in (FR-CRED-1, -2, -4)."""

import uuid

import pytest
from sqlalchemy.orm import Query

from app.core import security
from app.models.user import User

FOUR_BYTE = "\U0001F600"  # one character, four UTF-8 bytes


def _email(local: str | None = None) -> str:
    return f"{local or 'cred'}-{uuid.uuid4().hex}@example.com"


def _signup(client, password, email=None):
    return client.post("/auth/signup", json={"email": email or _email(), "password": password})


def _messages(response) -> str:
    return " ".join(error["msg"] for error in response.json()["detail"])


@pytest.mark.parametrize("password", ["", "1234567"])
def test_short_password_is_rejected(client, password):
    response = _signup(client, password)
    assert response.status_code == 422
    assert "at least 8 characters" in _messages(response)


@pytest.mark.parametrize("password", ["a" * 8, "a" * 72])
def test_boundary_lengths_are_accepted(client, password):
    assert _signup(client, password).status_code == 201


@pytest.mark.parametrize("password", ["a" * 73, FOUR_BYTE * 25])
def test_too_long_password_is_rejected_not_cut(client, password):
    email = _email()
    response = _signup(client, password, email)
    assert response.status_code == 422
    assert "at most 72 bytes" in _messages(response)
    # Nothing was stored, so the address is still free.
    assert _signup(client, "a-fine-password", email).status_code == 201


def test_no_composition_rules(client):
    assert _signup(client, "aaaaaaaa").status_code == 201


def test_duplicate_email_in_a_different_case_is_409(client):
    local = f"ada-{uuid.uuid4().hex}"
    assert _signup(client, "password-one", f"{local.capitalize()}@x.com").status_code == 201
    second = _signup(client, "password-two", f"{local}@x.com")
    assert second.status_code == 409
    assert second.json()["detail"] == "Email already registered"


def test_response_email_is_lower_case_and_trimmed(client):
    local = f"Mixed-{uuid.uuid4().hex}"
    response = _signup(client, "password-one", f"  {local}@Example.COM ")
    assert response.status_code == 201
    assert response.json()["email"] == f"{local}@example.com".lower()


def test_any_casing_signs_in_a_new_account(client):
    email = _email("lower")
    assert _signup(client, "password-one", email).status_code == 201
    for variant in (email, email.upper(), email.title()):
        login = client.post("/auth/login", json={"email": variant, "password": "password-one"})
        assert login.status_code == 200


def _legacy_user(db_session_factory, email: str, password: str) -> None:
    with db_session_factory() as session:
        # Bypasses hash_password on purpose: the legacy hash may predate the 72-byte rule.
        session.add(User(email=email, password_hash=security._pwd_context.hash(password)))
        session.commit()


def test_legacy_mixed_case_account_logs_in_with_any_casing(client, db_session_factory):
    local = f"Legacy-{uuid.uuid4().hex}"
    _legacy_user(db_session_factory, f"{local}@Example.com", "old-password")
    for variant in (f"{local}@Example.com", f"{local}@example.com".lower(), f"{local}@EXAMPLE.COM"):
        login = client.post("/auth/login", json={"email": variant, "password": "old-password"})
        assert login.status_code == 200, variant


@pytest.mark.parametrize("password", ["12345", "p" * 80])
def test_legacy_short_and_long_passwords_still_log_in(client, db_session_factory, password):
    email = _email("legacy")
    _legacy_user(db_session_factory, email, password)
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200


def test_signup_race_on_the_unique_index_is_409_not_500(client, monkeypatch):
    email = _email("race")
    assert _signup(client, "password-one", email).status_code == 201
    # The existence check misses (as it would for a concurrent request); the insert then
    # hits the lower(email) unique index.
    monkeypatch.setattr(Query, "first", lambda self: None)
    response = _signup(client, "password-two", email.upper())
    assert response.status_code == 409
    assert response.json()["detail"] == "Email already registered"


def test_hash_password_refuses_more_than_72_bytes():
    with pytest.raises(ValueError):
        security.hash_password("a" * 73)
    with pytest.raises(ValueError):
        security.hash_password(FOUR_BYTE * 25)
    assert security.hash_password("a" * 72)
