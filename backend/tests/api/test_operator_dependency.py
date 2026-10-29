"""010 T335: `require_operator` on a test-only router. Real (rolled-back) DB.

Operator status is read from the user row on every request (FR-AUTHZ-4): the token carries no
role claim, so flipping the flag changes the very next response for the SAME token."""

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import require_operator
from app.data.repositories.db import get_db
from app.models.user import User


@pytest.fixture
def operator_probe(db_session_factory):
    router = APIRouter()

    @router.get("/_probe/operator-only")
    def operator_only(user: User = Depends(require_operator)):
        return {"id": str(user.id)}

    app = FastAPI()
    app.include_router(router)

    def _override_get_db():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)


def _set_operator(db_session_factory, user_id, value: bool) -> None:
    with db_session_factory() as session:
        session.get(User, user_id).is_operator = value
        session.commit()


def test_no_token_is_401(operator_probe):
    response = operator_probe.get("/_probe/operator-only")
    assert response.status_code == 401


def test_bad_token_is_401(operator_probe):
    response = operator_probe.get("/_probe/operator-only", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_non_operator_is_403(operator_probe, authed_user):
    response = operator_probe.get("/_probe/operator-only", headers=authed_user["headers"])
    assert response.status_code == 403
    assert response.json() == {"detail": "Operator access required"}


def test_new_users_are_not_operators(authed_user, db_session_factory):
    with db_session_factory() as session:
        assert session.get(User, authed_user["user_id"]).is_operator is False


def test_operator_is_200(operator_probe, authed_user, db_session_factory):
    _set_operator(db_session_factory, authed_user["user_id"], True)
    response = operator_probe.get("/_probe/operator-only", headers=authed_user["headers"])
    assert response.status_code == 200
    assert response.json() == {"id": str(authed_user["user_id"])}


def test_flag_change_applies_to_the_next_request_with_the_same_token(operator_probe, authed_user, db_session_factory):
    headers, user_id = authed_user["headers"], authed_user["user_id"]
    assert operator_probe.get("/_probe/operator-only", headers=headers).status_code == 403
    _set_operator(db_session_factory, user_id, True)
    assert operator_probe.get("/_probe/operator-only", headers=headers).status_code == 200
    _set_operator(db_session_factory, user_id, False)
    assert operator_probe.get("/_probe/operator-only", headers=headers).status_code == 403
