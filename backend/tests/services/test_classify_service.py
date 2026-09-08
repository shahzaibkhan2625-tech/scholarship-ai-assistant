"""`classify` service tests (T080): dispatches to the classify tool and
reshapes its output onto the scholarship's classification fields."""

from unittest.mock import patch

from app.tools.classify import ClassificationField, ClassificationResult
from app.services.classify import classify_candidate


def test_classify_candidate_maps_tool_output_onto_scholarship_fields():
    tool_result = ClassificationResult(
        degree_level=ClassificationField(value="PhD", confidence="verified"),
        funding_type=ClassificationField(value="fully_funded", confidence="verified"),
        provider_type=ClassificationField(value="gov", confidence="verified"),
        country=ClassificationField(value="germany", confidence="verified"),
        field=ClassificationField(value="computer science", confidence="inferred"),
    )

    with patch("app.services.classify.classify_tool", return_value=tool_result) as mock_tool:
        classified = classify_candidate("some scholarship description")

    mock_tool.assert_called_once_with("some scholarship description")
    assert classified.degree_level == "PhD"
    assert classified.degree_level_confidence == "verified"
    assert classified.funding_status == "fully_funded"
    assert classified.provider_type == "gov"
    assert classified.country == "germany"
    assert classified.field == "computer science"
    assert classified.field_confidence == "inferred"


def test_classify_candidate_passes_through_unknowns():
    tool_result = ClassificationResult()

    with patch("app.services.classify.classify_tool", return_value=tool_result):
        classified = classify_candidate("ambiguous text")

    assert classified.degree_level is None
    assert classified.degree_level_confidence == "unknown"
