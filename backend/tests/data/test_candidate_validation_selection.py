"""`candidate_validation_repo.list_pending_needing_validation` (002 US7/US8, T244 support).

Read-only selection for the monitor-run trigger: PENDING candidates with no
validation, or whose latest validation is older than `stale_before`. Returns
ids only (so the caller can end its read transaction before any network call).
Never-validated first, then stalest first. Real (rolled-back) DB; the shared DB
may hold real pending candidates, so assertions filter to this test's rows.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.data.repositories import candidate_validation_repo, source_repo
from app.models.source import CandidateSourceStatus
from app.schemas.source import CandidateSourceCreate

# Captured at import: the conftest autouse stub (monitor runs see no candidates by default)
# replaces the module attribute for every test, but THIS file tests the real query.
_REAL_SELECT = candidate_validation_repo.list_pending_needing_validation

NOW = datetime(2027, 6, 1, tzinfo=timezone.utc)
STALE_BEFORE = NOW - timedelta(days=7)


@pytest.fixture
def env(db_session_factory):
    session = db_session_factory()
    uid = uuid.uuid4().hex[:8]
    created: list[uuid.UUID] = []

    def make(label, *, status=CandidateSourceStatus.PENDING, validated_days_ago=None):
        candidate = source_repo.create_candidate_source(
            session, CandidateSourceCreate(url=f"https://sel-{label}-{uid}.example.org/scholarships")
        )
        if status != CandidateSourceStatus.PENDING:
            candidate.status = status
            session.commit()
        if validated_days_ago is not None:
            candidate_validation_repo.add(
                session, candidate.id, reachable=True, extractable=True, official_signals={},
                checked_at=NOW - timedelta(days=validated_days_ago),
            )
        created.append(candidate.id)
        return candidate.id

    def select(limit=10_000):
        ids = _REAL_SELECT(session, stale_before=STALE_BEFORE, limit=limit)
        return [i for i in ids if i in created]

    class Env:
        pass

    e = Env()
    e.session, e.make, e.select = session, make, select
    yield e
    session.close()


def test_never_validated_pending_candidate_is_selected(env):
    never = env.make("never")
    assert env.select() == [never]


def test_recent_validation_excludes_and_stale_validation_includes(env):
    fresh = env.make("fresh", validated_days_ago=1)
    stale = env.make("stale", validated_days_ago=30)
    selected = env.select()
    assert stale in selected and fresh not in selected


def test_only_the_latest_validation_counts(env):
    candidate = env.make("mixed", validated_days_ago=60)
    candidate_validation_repo.add(
        env.session, candidate, reachable=True, extractable=True, official_signals={},
        checked_at=NOW - timedelta(days=2),
    )
    assert candidate not in env.select()


def test_approved_and_rejected_candidates_are_never_selected(env):
    env.make("approved", status=CandidateSourceStatus.APPROVED)
    env.make("rejected", status=CandidateSourceStatus.REJECTED)
    assert env.select() == []


def test_never_validated_first_then_stalest_first(env):
    newer_stale = env.make("newer", validated_days_ago=10)
    older_stale = env.make("older", validated_days_ago=90)
    never = env.make("never")
    assert env.select() == [never, older_stale, newer_stale]


def test_limit_is_applied(env):
    for label in ("a", "b", "c"):
        env.make(label)
    ids = _REAL_SELECT(env.session, stale_before=STALE_BEFORE, limit=1)
    assert len(ids) == 1


def test_selection_writes_nothing(env):
    candidate = env.make("ro")
    before = candidate_validation_repo.list_for_candidate(env.session, candidate)
    env.select()
    assert candidate_validation_repo.list_for_candidate(env.session, candidate) == before == []
