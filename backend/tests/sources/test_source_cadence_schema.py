"""T295: `extraction_rules['check_interval_minutes']` in the registry write schema (003 FR-CADENCE-3).
Pure schema tests, no database."""

import pytest
from pydantic import ValidationError

from app.schemas.source import SourceRegistryCreate

_BASE = dict(
    name="Example",
    source_type="gov",
    official_status="official",
    domain="www.example.org",
    access_method="web",
    reliability_level="high",
)


def _make(rules):
    return SourceRegistryCreate(**_BASE, extraction_rules=rules)


@pytest.mark.parametrize("value", [15, 60, 1440, 10080])
def test_accepts_an_integer_of_at_least_15(value):
    assert _make({"check_interval_minutes": value}).extraction_rules["check_interval_minutes"] == value


@pytest.mark.parametrize("value", [14, 0, -1, 60.5, "60", None, True, [60]])
def test_rejects_anything_else(value):
    with pytest.raises(ValidationError) as exc:
        _make({"check_interval_minutes": value})
    assert "check_interval_minutes" in str(exc.value)


def test_unknown_existing_keys_are_preserved_untouched():
    rules = {
        "list_page_url": "https://www.example.org/list",
        "robots_checked": True,
        "allowed_path_prefixes": ["/a", "/b"],
        "listing_complete": True,
        "some_future_key": {"nested": [1, 2]},
        "check_interval_minutes": 30,
    }
    assert _make(dict(rules)).extraction_rules == rules


def test_rules_without_the_key_still_validate():
    assert _make({}).extraction_rules == {}
    assert _make({"list_page_url": "https://www.example.org/x"}).extraction_rules == {
        "list_page_url": "https://www.example.org/x"
    }
