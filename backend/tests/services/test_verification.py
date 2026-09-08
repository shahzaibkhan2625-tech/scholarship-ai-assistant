"""`verification` service tests (T082, Blueprint §32): lifecycle_status is
never 'verified' without an official-source confirmation, and reflects
deadline/closing/freshness rules."""

from datetime import date, datetime, timedelta, timezone

from app.models.scholarship import LifecycleStatus
from app.services.verification import VerificationInput, compute_lifecycle_status

_NOW = datetime(2026, 9, 8, tzinfo=timezone.utc)


def test_third_party_only_confirmation_never_verified():
    data = VerificationInput(
        retrieved_at=_NOW,
        last_verified_at=_NOW - timedelta(days=5),
        deadline=date(2026, 12, 1),
        official_source_confirmed=False,
        now=_NOW,
    )

    status = compute_lifecycle_status(data)

    assert status != LifecycleStatus.VERIFIED
    assert status == LifecycleStatus.UNVERIFIED


def test_first_sighting_without_official_confirmation_is_newly_discovered():
    data = VerificationInput(
        retrieved_at=_NOW,
        last_verified_at=None,
        deadline=date(2026, 12, 1),
        official_source_confirmed=False,
        now=_NOW,
    )

    assert compute_lifecycle_status(data) == LifecycleStatus.NEWLY_DISCOVERED


def test_official_confirmation_marks_verified():
    data = VerificationInput(
        retrieved_at=_NOW - timedelta(days=1),
        last_verified_at=_NOW,
        deadline=date(2026, 12, 1),
        official_source_confirmed=True,
        now=_NOW,
    )

    assert compute_lifecycle_status(data) == LifecycleStatus.VERIFIED


def test_passed_deadline_is_expired_even_if_officially_confirmed():
    data = VerificationInput(
        retrieved_at=_NOW,
        last_verified_at=_NOW,
        deadline=date(2026, 1, 1),
        official_source_confirmed=True,
        now=_NOW,
    )

    assert compute_lifecycle_status(data) == LifecycleStatus.EXPIRED


def test_source_unavailable_overrides_everything():
    data = VerificationInput(
        retrieved_at=_NOW,
        last_verified_at=_NOW,
        deadline=date(2026, 12, 1),
        official_source_confirmed=True,
        source_unavailable=True,
        now=_NOW,
    )

    assert compute_lifecycle_status(data) == LifecycleStatus.SOURCE_UNAVAILABLE


def test_stale_when_not_reverified_within_window():
    data = VerificationInput(
        retrieved_at=_NOW,
        last_verified_at=_NOW - timedelta(days=200),
        deadline=date(2026, 12, 1),
        official_source_confirmed=True,
        now=_NOW,
    )

    assert compute_lifecycle_status(data) == LifecycleStatus.STALE
