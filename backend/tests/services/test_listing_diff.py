"""T179 (pure part): listing_diff — which stated fields differ from storage."""

from datetime import date
from types import SimpleNamespace

from app.models.scholarship import DegreeLevel, FundingStatus
from app.services.listing_diff import MONITORED_FIELDS, diff_listing_fields


def _stored(**overrides):
    base = dict(deadline=date(2027, 3, 1), funding_status=FundingStatus.FULLY_FUNDED, degree_level=DegreeLevel.PHD)
    return SimpleNamespace(**{**base, **overrides})


def test_monitored_fields_documented_set():
    assert MONITORED_FIELDS == ("deadline", "funding_status", "degree_level")


def test_identical_values_are_unchanged_even_as_iso_string_vs_date():
    raw = {"name": "X", "deadline": "2027-03-01", "funding_status": "fully_funded", "degree_level": "PhD"}
    assert diff_listing_fields(_stored(), raw) == {}


def test_each_monitored_field_change_is_detected_with_typed_value():
    raw = {"deadline": "2027-04-01", "funding_status": "tuition_only", "degree_level": "MS"}
    assert diff_listing_fields(_stored(), raw) == {
        "deadline": date(2027, 4, 1),
        "funding_status": FundingStatus.TUITION_ONLY,
        "degree_level": DegreeLevel.MS,
    }


def test_unstated_unparseable_or_unknown_values_are_never_a_change():
    raw = {"deadline": "not a date", "funding_status": "unknown", "degree_level": "wizard"}
    assert diff_listing_fields(_stored(), raw) == {}
    assert diff_listing_fields(_stored(), {"name": "X"}) == {}


def test_previously_empty_field_that_is_now_stated_is_a_change():
    assert diff_listing_fields(_stored(deadline=None), {"deadline": "2027-03-01"}) == {"deadline": date(2027, 3, 1)}


def test_non_monitored_fields_are_ignored():
    assert diff_listing_fields(_stored(), {"provider": "Someone Else", "country": "Mars"}) == {}
