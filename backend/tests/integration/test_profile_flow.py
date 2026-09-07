"""Integration test covering spec.md US1 Acceptance Scenarios 1-4."""


def test_scenario_1_new_profile_with_hard_constraint(authed_user) -> None:
    """Given a new authenticated user with no profile, When they enter
    academic info, target degree/field, countries, and mark nationality as a
    hard constraint, Then the profile is saved and nationality is retrievable
    as a hard constraint."""
    client, headers = authed_user["client"], authed_user["headers"]

    put_response = client.put(
        "/profile",
        headers=headers,
        json={
            "target_degree_level": "MS",
            "target_fields": ["Computer Science"],
            "target_countries": ["Germany", "Netherlands"],
        },
    )
    assert put_response.status_code == 200

    criteria_response = client.post(
        "/profile/criteria",
        headers=headers,
        json={"dimension": "nationality", "operator": "=", "value": "Pakistani", "kind": "hard_constraint"},
    )
    assert criteria_response.status_code == 200

    profile = client.get("/profile", headers=headers).json()
    assert profile["target_degree_level"] == "MS"
    assert profile["target_fields"] == ["Computer Science"]
    nationality_criteria = [c for c in profile["criteria"] if c["dimension"] == "nationality"]
    assert len(nationality_criteria) == 1
    assert nationality_criteria[0]["kind"] == "hard_constraint"


def test_scenario_2_partial_update_adds_test_score_without_full_reentry(authed_user) -> None:
    """Given an existing profile, When the user updates a field (adds a new
    IELTS score), Then the profile reflects the update without requiring a
    full profile re-entry."""
    client, headers = authed_user["client"], authed_user["headers"]

    client.put("/profile", headers=headers, json={"nationality": "Pakistani"})

    update_response = client.put(
        "/profile",
        headers=headers,
        json={"test_scores": [{"test_type": "IELTS", "status": "have", "score": 7.5}]},
    )
    assert update_response.status_code == 200
    body = update_response.json()

    # The earlier field (nationality) was not wiped by this second, unrelated update.
    assert body["nationality"] == "Pakistani"
    assert len(body["test_scores"]) == 1
    assert body["test_scores"][0]["test_type"] == "IELTS"
    assert body["test_scores"][0]["score"] == 7.5


def test_scenario_3_unanswered_field_shown_as_missing_not_omitted(authed_user) -> None:
    """Given a profile with an unanswered field (e.g. GRE status not
    provided), When the profile is viewed, Then that field is shown as
    missing/unknown rather than silently omitted or assumed."""
    client, headers = authed_user["client"], authed_user["headers"]

    client.put(
        "/profile",
        headers=headers,
        json={
            "nationality": "Pakistani",
            "country_of_residence": "Pakistan",
            "target_degree_level": "MS",
            "target_fields": ["Computer Science"],
            "education_records": [{"gpa": 3.7, "gpa_scale": 4.0}],
        },
    )

    profile = client.get("/profile", headers=headers).json()
    missing_dimensions = {item["dimension"] for item in profile["missing_info"]}

    # test_scores (e.g. GRE) was never provided, so it must be surfaced.
    assert "test_scores" in missing_dimensions
    # Fields that were answered are not incorrectly flagged as missing.
    assert "nationality" not in missing_dimensions


def test_scenario_4_reclassifying_criterion_updates_same_row(authed_user) -> None:
    """Given an existing profile, When the user reclassifies a criterion
    from soft preference to hard constraint, Then subsequent matching treats
    it accordingly (i.e. the same criterion row now reports the new kind,
    not a duplicate)."""
    client, headers = authed_user["client"], authed_user["headers"]

    created = client.post(
        "/profile/criteria",
        headers=headers,
        json={"dimension": "funding", "operator": ">=", "value": "full", "kind": "soft_preference"},
    )
    criterion_id = created.json()[0]["id"]

    reclassified = client.post(
        "/profile/criteria",
        headers=headers,
        json={
            "id": criterion_id,
            "dimension": "funding",
            "operator": ">=",
            "value": "full",
            "kind": "hard_constraint",
        },
    )
    assert reclassified.status_code == 200

    profile = client.get("/profile", headers=headers).json()
    funding_criteria = [c for c in profile["criteria"] if c["dimension"] == "funding"]

    assert len(funding_criteria) == 1  # updated in place, not duplicated
    assert funding_criteria[0]["kind"] == "hard_constraint"
