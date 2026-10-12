"""Contract tests: GET /scholarships/{id} (002 US5, FR-LIFECYCLE-1, contracts `/scholarships/{scholarship_id}`).
Read-only, bearer auth, minimal schema. Real (rolled-back) DB."""

import uuid
from datetime import date

import pytest

from app.data.repositories import scholarship_repo
from app.models.scholarship import LifecycleStatus, Scholarship, VerificationStatus

_KEYS = {"id", "name", "lifecycle_status", "verification_status", "last_verified_at", "deadline"}


@pytest.fixture
def session(db_session_factory):
    s = db_session_factory()
    yield s
    s.close()


def _make(session, **overrides):
    data = dict(
        name=f"Api Sch {uuid.uuid4().hex[:8]}", official_scholarship_url=f"https://api.example.test/{uuid.uuid4().hex}",
        lifecycle_status=LifecycleStatus.STALE, verification_status=VerificationStatus.VERIFIED,
    )
    return scholarship_repo.create(session, Scholarship(**{**data, **overrides}))


def test_requires_a_bearer_token(client):
    url = f"/scholarships/{uuid.uuid4()}"
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_unknown_id_is_404(authed_user):
    response = authed_user["client"].get(f"/scholarships/{uuid.uuid4()}", headers=authed_user["headers"])
    assert response.status_code == 404


def test_malformed_id_is_422(authed_user):
    response = authed_user["client"].get("/scholarships/not-a-uuid", headers=authed_user["headers"])
    assert response.status_code == 422


def test_returns_exactly_the_minimal_schema_with_one_lifecycle_value(authed_user, session):
    row = _make(session, deadline=date(2030, 1, 2))
    response = authed_user["client"].get(f"/scholarships/{row.id}", headers=authed_user["headers"])
    assert response.status_code == 200
    body = response.json()
    assert set(body) == _KEYS
    assert body["id"] == str(row.id) and body["name"] == row.name
    assert body["lifecycle_status"] == "stale" and body["verification_status"] == "verified"
    assert body["deadline"] == "2030-01-02" and body["last_verified_at"] is None
    assert body["lifecycle_status"] in {s.value for s in LifecycleStatus}


def test_route_is_read_only(authed_user, session):
    row = _make(session)
    for method in ("post", "put", "patch", "delete"):
        response = getattr(authed_user["client"], method)(f"/scholarships/{row.id}", headers=authed_user["headers"])
        assert response.status_code in (404, 405, 422)
