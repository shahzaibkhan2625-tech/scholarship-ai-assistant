"""`conflict_resolution` service tests (T083, Blueprint §32): official beats
third-party, more-recent beats older among equals, and a genuine tie is
marked 'conflicting' with both values retained — never silently picked."""

from datetime import datetime, timezone

from app.services.conflict_resolution import SourceValue, resolve_conflict

_T1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_T2 = datetime(2026, 6, 1, tzinfo=timezone.utc)


def test_official_beats_third_party():
    candidates = [
        SourceValue(source_id="official-1", value="EUR 1200/month", is_official=True, retrieved_at=_T1, reliability="high"),
        SourceValue(source_id="thirdparty-1", value="EUR 1000/month", is_official=False, retrieved_at=_T2, reliability="medium"),
    ]

    resolution = resolve_conflict(candidates)

    assert resolution.value == "EUR 1200/month"
    assert resolution.value_status == "known"
    assert resolution.resolved_by == "official"
    assert resolution.winning_source_id == "official-1"


def test_two_official_sources_pick_more_recent():
    candidates = [
        SourceValue(source_id="official-old", value="EUR 1000/month", is_official=True, retrieved_at=_T1, reliability="high"),
        SourceValue(source_id="official-new", value="EUR 1200/month", is_official=True, retrieved_at=_T2, reliability="high"),
    ]

    resolution = resolve_conflict(candidates)

    assert resolution.value == "EUR 1200/month"
    assert resolution.resolved_by == "recency"
    assert resolution.winning_source_id == "official-new"


def test_equally_fresh_equally_official_sources_produce_conflicting_with_both_retained():
    candidates = [
        SourceValue(source_id="official-a", value="EUR 1000/month", is_official=True, retrieved_at=_T1, reliability="high"),
        SourceValue(source_id="official-b", value="EUR 1200/month", is_official=True, retrieved_at=_T1, reliability="high"),
    ]

    resolution = resolve_conflict(candidates)

    assert resolution.value_status == "conflicting"
    assert resolution.value is None
    assert resolution.winning_source_id is None
    assert {c.source_id for c in resolution.conflicting_values} == {"official-a", "official-b"}
    assert {c.value for c in resolution.conflicting_values} == {"EUR 1000/month", "EUR 1200/month"}


def test_identical_values_are_not_a_conflict():
    candidates = [
        SourceValue(source_id="s1", value="EUR 1200/month", is_official=True, retrieved_at=_T1, reliability="high"),
        SourceValue(source_id="s2", value="EUR 1200/month", is_official=False, retrieved_at=_T2, reliability="low"),
    ]

    resolution = resolve_conflict(candidates)

    assert resolution.value_status == "known"
    assert resolution.value == "EUR 1200/month"


def test_higher_reliability_breaks_tie_among_equally_recent_non_official_sources():
    candidates = [
        SourceValue(source_id="low-rel", value="EUR 1000/month", is_official=False, retrieved_at=_T1, reliability="low"),
        SourceValue(source_id="high-rel", value="EUR 1200/month", is_official=False, retrieved_at=_T1, reliability="high"),
    ]

    resolution = resolve_conflict(candidates)

    assert resolution.value == "EUR 1200/month"
    assert resolution.resolved_by == "reliability"
    assert resolution.winning_source_id == "high-rel"
