"""003 T310 (FR-RULES-1/2/4, R-11): the declarative rule engine over the committed HEC listing fixture.

`tests/fixtures/hec/listing.html` is a trimmed copy of the real HEC listing page (only the card container kept),
fetched once by plain HTTP on 2026-11-27 while implementing this task. NOTE: the task text expected the page of
2026-10-25 (43 cards, 38 unique names); the live page had changed by then and has 38 cards, 37 unique names, so
the numbers asserted here are those of the committed fixture. No network, no provider."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.extraction_rules import DeterministicRules, rules_for_source
from app.tools import rule_extract
from app.tools.ground_check import ground_check

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hec" / "listing.html"
SOURCE_URL = "https://www.hec.gov.pk/english/scholarshipsgrants/Pages/default.aspx"

HEC_RULES = {
    "version": 1,
    "enabled": True,
    "item_selector": "ul.card-info-container li.card",
    "name": {"attribute": "data-position"},
    "detail_url": {"selector": "a.read-more-arrow", "attribute": "href"},
    "provider_from_registry": True,
    "min_items": 20,
}


@pytest.fixture(scope="module")
def html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def _rules(**overrides) -> DeterministicRules:
    return DeterministicRules.model_validate({**HEC_RULES, **overrides})


def _run(html, rules=None, **kw):
    return rule_extract.rule_extract(html, rules or _rules(), source_url=SOURCE_URL, **kw)


def test_the_fixture_has_the_cards_the_page_had(html):
    result = _run(html)
    assert result.raw_count == 38  # cards on the committed page
    assert result.unique_count == 37  # "Alumni Gateway" appears twice
    assert len(result.output.candidates) == 37


def test_names_come_from_the_data_position_attribute_in_page_order_without_duplicates(html):
    names = [c.name for c in _run(html).output.candidates]
    assert names[0] == "Faculty Development Programme for Pakistani Universities"
    assert names.count("Alumni Gateway") == 1  # order-preserving de-duplication
    assert len(names) == len(set(n.lower() for n in names))


def test_html_entities_are_decoded(html):
    names = [c.name for c in _run(html).output.candidates]
    assert "Allama Muhammad Iqbal Scholarship for Central Asia & Friendly Countries" in names
    assert not any("&amp;" in n for n in names)


def test_detail_urls_are_resolved_and_restricted_to_the_source_domain(html):
    candidates = _run(html).output.candidates
    first = candidates[0]
    assert first.detail_url == "https://www.hec.gov.pk/english/scholarshipsgrants/Pages/FDP.aspx"
    assert all(c.detail_url is None or c.detail_url.startswith("https://www.hec.gov.pk/") for c in candidates)
    assert sum(c.detail_url is not None for c in candidates) == len(candidates)


def test_an_off_domain_or_unsafe_link_is_dropped_but_the_item_is_kept():
    page = (
        "<ul class='x'>"
        "<li class='c' data-n='Alpha Award'>Alpha Award <a href='https://evil.example.net/a'>go</a></li>"
        "<li class='c' data-n='Beta Award'>Beta Award <a href='javascript:alert(1)'>go</a></li>"
        "<li class='c' data-n='Gamma Award'>Gamma Award <a href='/ok/gamma'>go</a></li>"
        "</ul>"
    )
    rules = DeterministicRules.model_validate(
        {"version": 1, "enabled": True, "item_selector": "li.c", "name": {"attribute": "data-n"},
         "detail_url": {"selector": "a", "attribute": "href"}, "min_items": 1}
    )
    out = rule_extract.rule_extract(page, rules, source_url="https://www.hec.gov.pk/list").output
    assert [(c.name, c.detail_url) for c in out.candidates] == [
        ("Alpha Award", None), ("Beta Award", None), ("Gamma Award", "https://www.hec.gov.pk/ok/gamma"),
    ]


def test_every_name_is_grounded_at_one_against_parser_decoded_text(html):
    result = _run(html)
    assert result.output.rejected_count == 0
    assert result.output.accepted_count == 37
    text = rule_extract.decoded_text(html)
    for candidate in result.output.candidates:
        assert ground_check(candidate.name, [text], threshold=1.0).grounded, candidate.name


def test_a_name_that_is_not_on_the_page_is_rejected_by_the_grounding_gate():
    page = "<ul><li class='c' data-n='Real Award'>Real Award</li><li class='c' data-n='Invented Prize'>other words</li></ul>"
    rules = DeterministicRules.model_validate(
        {"version": 1, "enabled": True, "item_selector": "li.c", "name": {"attribute": "data-n"}, "min_items": 1}
    )
    out = rule_extract.rule_extract(page, rules, source_url=SOURCE_URL).output
    assert [c.name for c in out.candidates] == ["Real Award"]
    assert out.rejected_names == ["Invented Prize"] and out.rejected_count == 1


def test_the_name_can_come_from_a_selector_text_instead_of_an_attribute(html):
    rules = _rules(name={"selector": "div.desc"})
    names = [c.name for c in _run(html, rules).output.candidates]
    assert names[0] == "Faculty Development Programme for Pakistani Universities"


def test_provider_is_the_registry_organization_only_when_set_and_asked_for(html):
    with_org = _run(html, organization="Higher Education Commission").output.candidates
    assert {c.provider for c in with_org} == {"Higher Education Commission"}
    assert {c.provider for c in _run(html).output.candidates} == {None}  # organization not given
    no_flag = _run(html, _rules(provider_from_registry=False), organization="Higher Education Commission")
    assert {c.provider for c in no_flag.output.candidates} == {None}
    # nothing else is guessed (Principle I)
    c = with_org[0]
    assert (c.country, c.field, c.degree_level, c.deadline, c.funding_status) == (None, None, None, None, "unknown")


def test_min_items_decides_whether_the_rules_matched(html):
    assert _run(html).matched is True
    assert _run(html, _rules(min_items=50)).matched is False  # 37 < 50: the caller falls back to the LLM
    nothing = rule_extract.rule_extract("<html><body>no cards</body></html>", _rules(), source_url=SOURCE_URL)
    assert nothing.matched is False and nothing.unique_count == 0


@pytest.mark.parametrize(
    "previous, expected",
    [(None, None), (37, None), (30, None), (80, "below_low_ratio"), (10, "above_high_ratio"), (0, None)],
)
def test_anomaly_flags_below_half_or_above_double_the_previous_count(html, previous, expected):
    assert _run(html, previous_count=previous).anomaly == expected


def test_anomaly_ratios_can_be_set_per_rule_set(html):
    assert _run(html, _rules(anomaly_low_ratio=0.9), previous_count=50).anomaly == "below_low_ratio"


def test_nothing_is_invoked_for_a_source_with_no_rules():
    assert rules_for_source({}) is None
    assert rules_for_source({"list_page_url": "https://x.example.com"}) is None
    assert rules_for_source({"deterministic": {**HEC_RULES, "enabled": False}}) is None  # present but not enabled
    assert rules_for_source({"deterministic": HEC_RULES}) is not None


def test_the_rule_schema_has_no_category_exclusion_field():
    assert not any("categor" in name or "exclu" in name for name in DeterministicRules.model_fields)
    with pytest.raises(ValidationError):
        DeterministicRules.model_validate({**HEC_RULES, "exclude_categories": ["Support Programs"]})


@pytest.mark.parametrize(
    "bad",
    [
        {"version": 2},
        {"item_selector": ""},
        {"name": {}},
        {"name": {"attribute": "a", "selector": "b"}},
        {"min_items": 0},
        {"anomaly_low_ratio": 0},
        {"anomaly_high_ratio": 0.5},
        {"unknown_key": 1},
    ],
)
def test_invalid_rule_sets_are_rejected(bad):
    with pytest.raises(ValidationError):
        DeterministicRules.model_validate({**HEC_RULES, **bad})


def test_an_invalid_selector_is_rejected_when_the_rules_are_validated():
    with pytest.raises(ValidationError):
        DeterministicRules.model_validate({**HEC_RULES, "item_selector": "li[[["})
