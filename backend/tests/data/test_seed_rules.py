"""003 T313 (plan decision 7): the seed file carries a HEC `deterministic` rule set, DISABLED.

The seed schema accepts it, the rule set is `enabled: false` (HEC rules are switched on only after the parity check
and the owner's approval, with the `set` command, never by re-seeding), no category-exclusion field exists, and the
repository's secret scan still passes. Pure: reads the YAML, no DB."""

import json
from pathlib import Path

from app.schemas.extraction_rules import DeterministicRules
from app.services.source_registry_seed import load_seed_rows

BACKEND = Path(__file__).resolve().parents[2]


def _hec():
    return next(row for row in load_seed_rows() if row.domain == "www.hec.gov.pk")


def test_the_hec_seed_carries_a_disabled_deterministic_rule_set():
    rules = _hec().extraction_rules["deterministic"]
    assert rules["enabled"] is False
    parsed = DeterministicRules.model_validate(rules)  # the seed schema accepts it
    assert parsed.item_selector and parsed.name.attribute == "data-position"


def test_the_existing_hec_seed_keys_are_untouched():
    rules = _hec().extraction_rules
    assert rules["allowed_path_prefixes"] == ["/english/scholarshipsgrants/"]
    assert rules["list_page_url"] == "https://www.hec.gov.pk/english/scholarshipsgrants/Pages/default.aspx"
    assert _hec().listing_page_url == rules["list_page_url"]


def test_no_hec_category_exclusion_field_exists():
    text = json.dumps(_hec().extraction_rules).lower()
    assert "categor" not in text and "exclu" not in text
    assert not any("categor" in name or "exclu" in name for name in DeterministicRules.model_fields)


def test_only_hec_has_rules_and_eacea_stays_without():
    for row in load_seed_rows():
        has = "deterministic" in row.extraction_rules
        assert has == (row.domain == "www.hec.gov.pk"), row.domain


def test_the_seed_rules_find_the_committed_fixture_cards():
    from app.tools import rule_extract

    html = (BACKEND / "tests" / "fixtures" / "hec" / "listing.html").read_text(encoding="utf-8")
    rules = DeterministicRules.model_validate({**_hec().extraction_rules["deterministic"], "enabled": True})
    result = rule_extract.rule_extract(html, rules, source_url="https://www.hec.gov.pk/english/scholarshipsgrants/Pages/default.aspx")
    assert result.matched and result.output.accepted_count == 37 and result.output.rejected_count == 0
