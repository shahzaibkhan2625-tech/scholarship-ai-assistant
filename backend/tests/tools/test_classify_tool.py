"""`classify` tool tests (T077, Blueprint §11, §31): rules-based first pass
resolves clear keyword patterns without touching the LLM; only genuinely
ambiguous dimensions fall through to the (mocked) LLM fallback call."""

from unittest.mock import patch

from app.tools.classify import classify


def test_clear_keywords_classify_via_rules_without_llm_call():
    text = (
        "Fully-funded PhD scholarship at the University of Bonn, Germany, "
        "for students in computer science, offered by the Ministry of Education."
    )

    with patch("app.tools.classify._llm_fallback_classify") as mock_llm:
        result = classify(text)

    mock_llm.assert_not_called()
    assert result.degree_level.value == "PhD"
    assert result.degree_level.confidence == "inferred"  # rules match != official confirmation (§14/§32)
    assert result.funding_type.value == "fully_funded"
    assert result.funding_type.confidence == "inferred"
    assert result.provider_type.value == "gov"
    assert result.country.value == "germany"
    assert result.field.value == "computer science"


def test_ambiguous_case_falls_through_to_llm_and_is_invoked_only_then():
    ambiguous_text = "A great opportunity for students who want to study abroad."

    with patch(
        "app.tools.classify._llm_fallback_classify",
        return_value={
            "degree_level": "MS",
            "funding_type": None,
            "provider_type": None,
            "country": None,
            "field": None,
        },
    ) as mock_llm:
        result = classify(ambiguous_text)

    mock_llm.assert_called_once()
    called_dimensions = mock_llm.call_args.args[1]
    assert set(called_dimensions) == {"degree_level", "funding_type", "provider_type", "country", "field"}
    assert result.degree_level.value == "MS"
    assert result.degree_level.confidence == "inferred"
    assert result.funding_type.value is None
    assert result.funding_type.confidence == "unknown"


def test_llm_only_invoked_for_the_unresolved_dimensions():
    text = "PhD program in Germany at a foundation-run institute."  # degree_level, country, provider_type all clear

    with patch(
        "app.tools.classify._llm_fallback_classify",
        return_value={"funding_type": "partially_funded", "field": "engineering"},
    ) as mock_llm:
        result = classify(text)

    mock_llm.assert_called_once()
    called_dimensions = set(mock_llm.call_args.args[1])
    assert called_dimensions == {"funding_type", "field"}
    assert result.degree_level.confidence == "inferred"
    assert result.country.confidence == "inferred"
    assert result.provider_type.confidence == "inferred"
    assert result.funding_type.value == "partially_funded"
    assert result.funding_type.confidence == "inferred"
    assert result.field.value == "engineering"
    assert result.field.confidence == "inferred"
