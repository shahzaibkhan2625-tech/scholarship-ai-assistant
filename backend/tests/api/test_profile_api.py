"""Contract tests: GET/PUT /profile, POST /profile/criteria,
POST /profile/import-cv."""

import io
from unittest.mock import patch

from app.schemas.profile import ProfileUpdateRequest


def test_get_profile_creates_and_returns_empty_profile(authed_user) -> None:
    client, headers = authed_user["client"], authed_user["headers"]

    response = client.get("/profile", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["nationality"] is None
    assert body["missing_info"] == [] or isinstance(body["missing_info"], list)
    assert body["criteria"] == []


def test_get_profile_requires_auth(authed_user) -> None:
    client = authed_user["client"]

    response = client.get("/profile")

    assert response.status_code == 401


def test_put_profile_partial_update(authed_user) -> None:
    client, headers = authed_user["client"], authed_user["headers"]

    response = client.put(
        "/profile",
        headers=headers,
        json={"nationality": "Pakistani", "target_degree_level": "MS"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["nationality"] == "Pakistani"
    assert body["target_degree_level"] == "MS"


def test_post_criteria_creates_and_returns_criterion(authed_user) -> None:
    client, headers = authed_user["client"], authed_user["headers"]

    response = client.post(
        "/profile/criteria",
        headers=headers,
        json={
            "dimension": "nationality",
            "operator": "=",
            "value": "Pakistani",
            "kind": "hard_constraint",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["kind"] == "hard_constraint"
    assert body[0]["dimension"] == "nationality"


def test_post_criteria_rejects_invalid_kind(authed_user) -> None:
    client, headers = authed_user["client"], authed_user["headers"]

    response = client.post(
        "/profile/criteria",
        headers=headers,
        json={
            "dimension": "nationality",
            "operator": "=",
            "value": "Pakistani",
            "kind": "not_a_real_kind",
        },
    )

    assert response.status_code == 422


def test_import_cv_returns_suggestions_without_persisting(authed_user) -> None:
    client, headers = authed_user["client"], authed_user["headers"]
    suggested = ProfileUpdateRequest(nationality="Pakistani", target_degree_level="MS")

    with patch("app.api.profile.extract_profile_from_text", return_value=suggested):
        response = client.post(
            "/profile/import-cv",
            headers=headers,
            files={"file": ("cv.txt", io.BytesIO(b"John Doe, Pakistani, applying for MS."), "text/plain")},
        )

    assert response.status_code == 200
    assert response.json()["nationality"] == "Pakistani"

    # Confirm nothing was auto-saved to the actual profile.
    profile_response = client.get("/profile", headers=headers)
    assert profile_response.json()["nationality"] is None
