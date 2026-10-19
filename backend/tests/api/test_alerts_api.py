"""`/alerts*` API tests (002 US2, T186 + T250). Alerts are inserted directly on
the test's rolled-back connection; no monitoring run is involved."""

import uuid
from datetime import datetime, timezone

import pytest

from app.models.alert import Alert
from tests.alert_helpers import make_scholarship


@pytest.fixture
def seed(db_session_factory):
    session = db_session_factory()

    def alert_for(user_id, *, read=False) -> Alert:
        scholarship = make_scholarship(session)
        row = Alert(
            user_id=user_id,
            scholarship_id=scholarship.id,
            change_summary={"kind": "new_match", "fields": [], "scholarship_name": scholarship.name},
            channels_requested=["in_app"],
            delivered_in_app_at=datetime.now(timezone.utc),
            read_at=datetime.now(timezone.utc) if read else None,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row

    yield alert_for
    session.close()


def _second_user(client):
    email = f"test-{uuid.uuid4().hex}@example.com"
    signup = client.post("/auth/signup", json={"email": email, "password": "s3cret-pass"})
    token = client.post("/auth/login", json={"email": email, "password": "s3cret-pass"}).json()["access_token"]
    return uuid.UUID(signup.json()["id"]), {"Authorization": f"Bearer {token}"}


def test_alert_routes_require_auth(client):
    for method, path in (
        ("get", "/alerts"),
        ("get", "/alerts/preferences"),
        ("put", "/alerts/preferences"),
        ("post", f"/alerts/{uuid.uuid4()}/read"),
    ):
        assert getattr(client, method)(path).status_code == 401


def test_list_returns_only_the_callers_alerts(authed_user, seed, db_session_factory):
    client, headers = authed_user["client"], authed_user["headers"]
    other_id, _ = _second_user(client)
    mine = seed(authed_user["user_id"])
    theirs = seed(other_id)

    body = client.get("/alerts", headers=headers).json()

    assert [a["id"] for a in body] == [str(mine.id)]
    assert str(theirs.id) not in {a["id"] for a in body}
    assert body[0]["change_summary"]["kind"] == "new_match"
    assert body[0]["read_at"] is None and body[0]["channels_requested"] == ["in_app"]
    with db_session_factory() as s:  # second user's rows are torn down by the outer rollback
        assert s.get(Alert, theirs.id) is not None


def test_preferences_default_when_no_row_then_partial_put_persists(authed_user):
    client, headers = authed_user["client"], authed_user["headers"]

    assert client.get("/alerts/preferences", headers=headers).json() == {"in_app_enabled": True, "email_enabled": True}

    put = client.put("/alerts/preferences", headers=headers, json={"email_enabled": False})
    assert put.status_code == 200
    assert put.json() == {"in_app_enabled": True, "email_enabled": False}
    assert client.get("/alerts/preferences", headers=headers).json() == {"in_app_enabled": True, "email_enabled": False}

    client.put("/alerts/preferences", headers=headers, json={"in_app_enabled": False})
    assert client.get("/alerts/preferences", headers=headers).json() == {"in_app_enabled": False, "email_enabled": False}


def test_unread_only_filters_on_read_at(authed_user, seed):
    client, headers = authed_user["client"], authed_user["headers"]
    unread = seed(authed_user["user_id"])
    read = seed(authed_user["user_id"], read=True)

    everything = {a["id"] for a in client.get("/alerts", headers=headers).json()}
    only_unread = client.get("/alerts", headers=headers, params={"unread_only": "true"}).json()
    explicit_false = {a["id"] for a in client.get("/alerts", headers=headers, params={"unread_only": "false"}).json()}

    assert everything == {str(unread.id), str(read.id)} == explicit_false
    assert [a["id"] for a in only_unread] == [str(unread.id)]


# --- T250: mark read ----------------------------------------------------------------


def test_mark_read_sets_read_at_once_and_is_idempotent(authed_user, seed):
    client, headers = authed_user["client"], authed_user["headers"]
    alert = seed(authed_user["user_id"])

    first = client.post(f"/alerts/{alert.id}/read", headers=headers)
    assert first.status_code == 200
    stamp = first.json()["read_at"]
    assert stamp is not None and first.json()["id"] == str(alert.id)

    second = client.post(f"/alerts/{alert.id}/read", headers=headers)
    assert second.status_code == 200 and second.json()["read_at"] == stamp


def test_mark_read_removes_alert_from_unread_only_but_not_default_list(authed_user, seed):
    client, headers = authed_user["client"], authed_user["headers"]
    alert = seed(authed_user["user_id"])

    client.post(f"/alerts/{alert.id}/read", headers=headers)

    assert client.get("/alerts", headers=headers, params={"unread_only": "true"}).json() == []
    assert [a["id"] for a in client.get("/alerts", headers=headers).json()] == [str(alert.id)]


def test_mark_read_other_users_or_unknown_alert_is_404_and_untouched(authed_user, seed, db_session_factory):
    client, headers = authed_user["client"], authed_user["headers"]
    other_id, other_headers = _second_user(client)
    theirs = seed(other_id)

    assert client.post(f"/alerts/{theirs.id}/read", headers=headers).status_code == 404
    assert client.post(f"/alerts/{uuid.uuid4()}/read", headers=headers).status_code == 404

    with db_session_factory() as s:
        assert s.get(Alert, theirs.id).read_at is None
    assert client.post(f"/alerts/{theirs.id}/read", headers=other_headers).status_code == 200
