"""010 US1 T351: no HTTP surface can grant or reveal operator status (FR-AUTHZ-3).
Operator status changes only through the server-side CLI. Real (rolled-back) DB for the
sign-up / profile cases; the OpenAPI check needs none."""

import uuid

from app.models.user import User


def test_openapi_has_no_operator_role_or_admin_path():
    from app.main import app

    banned = ("operator", "role", "admin")
    offending = [path for path in app.openapi()["paths"] if any(word in path.lower() for word in banned)]
    assert offending == []


def test_signup_ignores_is_operator(client, db_session_factory):
    email = f"test-{uuid.uuid4().hex}@example.com"
    response = client.post(
        "/auth/signup", json={"email": email, "password": "s3cret-pass", "is_operator": True}  # allow-secret
    )
    assert response.status_code == 201
    assert "is_operator" not in response.json()
    with db_session_factory() as session:
        assert session.get(User, uuid.UUID(response.json()["id"])).is_operator is False


def test_profile_update_ignores_is_operator(client, make_user, db_session_factory):
    user = make_user()
    response = client.put("/profile", json={"name": "Ada", "is_operator": True}, headers=user["headers"])
    assert response.status_code < 300, response.text
    assert "is_operator" not in response.text
    with db_session_factory() as session:
        assert session.get(User, user["id"]).is_operator is False


def test_auth_responses_never_include_is_operator(client):
    email = f"test-{uuid.uuid4().hex}@example.com"
    signup = client.post("/auth/signup", json={"email": email, "password": "s3cret-pass"})  # allow-secret
    login = client.post("/auth/login", json={"email": email, "password": "s3cret-pass"})  # allow-secret
    assert login.status_code == 200
    for response in (signup, login):
        assert "is_operator" not in response.text
