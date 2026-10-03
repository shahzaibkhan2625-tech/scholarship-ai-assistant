"""T148: pure lifecycle transition service (FR-LIFECYCLE-1..5).

`services/lifecycle.py` is pure: no DB, no clock (`now` is passed in), no LLM.
Both evaluators return the NEW `LifecycleStatus`, or `None` when nothing
changes (so callers never write a spurious transition).

Locked decisions under test:
- Q3: confirmed CLOSED/EXPIRED beat source-unavailable — a failed fetch never
  overwrites them.
- Freshness: VERIFIED/UNVERIFIED/UPDATED/REOPENED can go STALE; everything
  else (incl. NEWLY_DISCOVERED, handled by ingestion) returns None.
- The service never returns UPDATED or NEWLY_DISCOVERED (US1 diff step /
  ingestion own those).
- O-3: leaving STALE/SOURCE_UNAVAILABLE after a successful re-check lands on
  VERIFIED if the record's verification_status is VERIFIED, else UNVERIFIED.
"""

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.scholarship import LifecycleStatus as LS
from app.models.scholarship import VerificationStatus as VS
from app.services import lifecycle, verification
from app.services.lifecycle import (
    DEFAULT_FRESHNESS_WINDOW_DAYS,
    FetchOutcome,
    ListedState,
    evaluate_fetch_outcome,
    evaluate_freshness,
)

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
ALL_STATUSES = list(LS)
STALE_ELIGIBLE = {LS.VERIFIED, LS.UNVERIFIED, LS.UPDATED, LS.REOPENED}

# --- builders ----------------------------------------------------------------


def _sch(status=LS.VERIFIED, verification=VS.VERIFIED, last_verified_days_ago: float | None = 1):
    return SimpleNamespace(
        lifecycle_status=status,
        verification_status=verification,
        last_verified_at=None if last_verified_days_ago is None else NOW - timedelta(days=last_verified_days_ago),
    )


def _src(window=None):
    return SimpleNamespace(freshness_window_days=window)


def _out(ok=True, exhausted=False, grounded=True, listed=None):
    return FetchOutcome(
        fetch_succeeded=ok, retries_exhausted=exhausted, extraction_grounded=grounded, listed_state=listed
    )


OPEN = _out(listed=ListedState.OPEN)
CLOSED = _out(listed=ListedState.CLOSED)
ABSENT = _out(listed=ListedState.ABSENT)
DEAD = _out(ok=False, exhausted=True, grounded=False)
RETRYING = _out(ok=False, exhausted=False, grounded=False)

# --- module shape ------------------------------------------------------------


def test_default_window_equals_verification_stale_threshold():
    assert DEFAULT_FRESHNESS_WINDOW_DAYS == verification._STALE_AFTER_DAYS


def test_fetch_outcome_is_frozen_with_documented_defaults():
    o = FetchOutcome(fetch_succeeded=True)
    assert (o.retries_exhausted, o.extraction_grounded, o.listed_state) == (False, False, None)
    with pytest.raises(Exception):  # noqa: B017 - FrozenInstanceError
        o.fetch_succeeded = False


def test_listed_state_members():
    assert {m.name for m in ListedState} == {"OPEN", "CLOSED", "ABSENT"}


# --- evaluate_freshness ------------------------------------------------------


@pytest.mark.parametrize("status", ALL_STATUSES)
def test_freshness_table_over_all_statuses(status):
    result = evaluate_freshness(_sch(status, last_verified_days_ago=10_000), _src(1), NOW)
    assert result == (LS.STALE if status in STALE_ELIGIBLE else None)


@pytest.mark.parametrize("status", sorted(STALE_ELIGIBLE, key=str))
def test_freshness_boundary_exactly_window_vs_window_plus_one(status):
    assert evaluate_freshness(_sch(status, last_verified_days_ago=7), _src(7), NOW) is None
    assert evaluate_freshness(_sch(status, last_verified_days_ago=8), _src(7), NOW) == LS.STALE


def test_freshness_uses_default_window_when_source_has_none():
    d = DEFAULT_FRESHNESS_WINDOW_DAYS
    assert evaluate_freshness(_sch(last_verified_days_ago=d), _src(None), NOW) is None
    assert evaluate_freshness(_sch(last_verified_days_ago=d + 1), _src(None), NOW) == LS.STALE


def test_freshness_per_source_window_overrides_default():
    s = _sch(last_verified_days_ago=10)
    assert evaluate_freshness(s, _src(7), NOW) == LS.STALE
    assert evaluate_freshness(s, _src(DEFAULT_FRESHNESS_WINDOW_DAYS + 50), NOW) is None


def test_freshness_last_verified_none_returns_none():
    s = _sch(LS.UNVERIFIED, VS.UNVERIFIED, last_verified_days_ago=None)
    assert evaluate_freshness(s, _src(1), NOW) is None


def test_freshness_requires_timezone_aware_now():
    with pytest.raises(ValueError):
        evaluate_freshness(_sch(), _src(), datetime(2026, 10, 4, 12, 0))


def test_freshness_depends_only_on_the_now_argument():
    s = _sch(last_verified_days_ago=40)
    assert evaluate_freshness(s, _src(30), NOW) == LS.STALE
    assert evaluate_freshness(s, _src(30), NOW - timedelta(days=20)) is None


# --- evaluate_fetch_outcome: rules 1-2 (failed fetch) -------------------------

_PRESERVED = {LS.SOURCE_UNAVAILABLE, LS.CLOSED, LS.EXPIRED}


@pytest.mark.parametrize("status", [s for s in ALL_STATUSES if s not in _PRESERVED])
def test_rule1_retry_exhaustion_yields_source_unavailable(status):
    assert evaluate_fetch_outcome(_sch(status), DEAD) == LS.SOURCE_UNAVAILABLE


@pytest.mark.parametrize("status", sorted(_PRESERVED, key=str))
def test_rule2_q3_dead_fetch_never_overwrites_closed_expired_or_itself(status):
    assert evaluate_fetch_outcome(_sch(status), DEAD) is None


@pytest.mark.parametrize("status", ALL_STATUSES)
def test_rule2_failed_fetch_not_exhausted_changes_nothing(status):
    assert evaluate_fetch_outcome(_sch(status), RETRYING) is None


@pytest.mark.parametrize("status", ALL_STATUSES)
@pytest.mark.parametrize("listed", [None, *ListedState])
def test_rule2_failed_fetch_never_yields_expired_or_closed(status, listed):
    for exhausted in (True, False):
        out = _out(ok=False, exhausted=exhausted, grounded=True, listed=listed)
        assert evaluate_fetch_outcome(_sch(status), out) in (None, LS.SOURCE_UNAVAILABLE)


# --- rule 3 (ungrounded) ------------------------------------------------------


@pytest.mark.parametrize("status", ALL_STATUSES)
@pytest.mark.parametrize("listed", [None, *ListedState])
def test_rule3_ungrounded_extraction_never_closes_or_exits_anything(status, listed):
    assert evaluate_fetch_outcome(_sch(status), _out(grounded=False, listed=listed)) is None


# --- rule 4 (closing) ---------------------------------------------------------


@pytest.mark.parametrize("outcome", [CLOSED, ABSENT], ids=["closed", "absent"])
@pytest.mark.parametrize("status", ALL_STATUSES)
def test_rule4_table_over_all_statuses(status, outcome):
    expected = None if status in (LS.CLOSED, LS.EXPIRED) else LS.CLOSED
    assert evaluate_fetch_outcome(_sch(status), outcome) == expected


# --- rule 5 (reopen) ----------------------------------------------------------


def test_rule5_closed_listed_open_becomes_reopened_never_newly_discovered():
    result = evaluate_fetch_outcome(_sch(LS.CLOSED), OPEN)
    assert result == LS.REOPENED and result != LS.NEWLY_DISCOVERED


# --- rule 6 (O-3 exits) -------------------------------------------------------


@pytest.mark.parametrize("status", [LS.STALE, LS.SOURCE_UNAVAILABLE])
def test_rule6_exit_to_verified_when_confirmed(status):
    assert evaluate_fetch_outcome(_sch(status, VS.VERIFIED), OPEN) == LS.VERIFIED


@pytest.mark.parametrize("status", [LS.STALE, LS.SOURCE_UNAVAILABLE])
@pytest.mark.parametrize("verification", [VS.UNVERIFIED, VS.CONFLICTING])
def test_rule6_exit_to_unverified_when_not_confirmed(status, verification):
    assert evaluate_fetch_outcome(_sch(status, verification), OPEN) == LS.UNVERIFIED


# --- rule 7 -------------------------------------------------------------------


def test_rule7_unverified_becomes_verified_when_confirmed():
    assert evaluate_fetch_outcome(_sch(LS.UNVERIFIED, VS.VERIFIED), OPEN) == LS.VERIFIED


def test_rule7_unverified_stays_when_not_confirmed():
    assert evaluate_fetch_outcome(_sch(LS.UNVERIFIED, VS.UNVERIFIED), OPEN) is None


# --- rule 8 (everything else) -------------------------------------------------


@pytest.mark.parametrize("status", ALL_STATUSES)
def test_rule8_listed_state_none_is_not_evaluable(status):
    assert evaluate_fetch_outcome(_sch(status), _out(listed=None)) is None


@pytest.mark.parametrize("status", [LS.NEWLY_DISCOVERED, LS.VERIFIED, LS.UPDATED, LS.REOPENED, LS.EXPIRED])
def test_rule8_open_listing_leaves_these_unchanged(status):
    assert evaluate_fetch_outcome(_sch(status), OPEN) is None


# --- global invariants over all nine statuses ----------------------------------

_OUTCOMES = [OPEN, CLOSED, ABSENT, DEAD, RETRYING, _out(grounded=False), _out(listed=None)]


@pytest.mark.parametrize("status", ALL_STATUSES)
@pytest.mark.parametrize("verification", list(VS))
def test_never_updated_never_newly_discovered_never_expired_never_echoes_current(status, verification):
    for outcome in _OUTCOMES:
        result = evaluate_fetch_outcome(_sch(status, verification), outcome)
        assert result not in (LS.UPDATED, LS.NEWLY_DISCOVERED, LS.EXPIRED)
        assert result != status


@pytest.mark.parametrize("status", [LS.STALE, LS.SOURCE_UNAVAILABLE])
@pytest.mark.parametrize("verification", list(VS))
def test_exit_from_stale_or_unavailable_lands_only_on_verified_or_unverified(status, verification):
    assert evaluate_fetch_outcome(_sch(status, verification), OPEN) in (LS.VERIFIED, LS.UNVERIFIED)


def test_evaluators_do_not_mutate_inputs_and_are_deterministic():
    s = _sch(LS.STALE, VS.VERIFIED)
    before = dict(vars(s))
    results = {evaluate_fetch_outcome(s, OPEN) for _ in range(5)}
    evaluate_freshness(s, _src(1), NOW)
    assert results == {LS.VERIFIED} and vars(s) == before


# --- purity: banned imports (AST) ---------------------------------------------

_BANNED_PREFIXES = ("app.agents", "app.core.llm", "langchain", "google.genai")


def _lifecycle_source() -> Path:
    return Path(lifecycle.__file__)


def _imported_modules(tree: ast.Module) -> list[str]:
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            mods.append(base)
            # `from app.core import llm` -> app.core.llm
            mods += [f"{base}.{alias.name}" for alias in node.names]
    return mods


def test_lifecycle_imports_no_agent_llm_or_langchain_modules():
    tree = ast.parse(_lifecycle_source().read_text(encoding="utf-8"))
    offenders = [m for m in _imported_modules(tree) if m.startswith(_BANNED_PREFIXES)]
    assert offenders == []


def test_lifecycle_imports_no_db_layer():
    tree = ast.parse(_lifecycle_source().read_text(encoding="utf-8"))
    offenders = [m for m in _imported_modules(tree) if m.split(".")[0] == "sqlalchemy" or m.startswith("app.data")]
    assert offenders == []


def test_lifecycle_never_reads_the_clock():
    src = _lifecycle_source().read_text(encoding="utf-8")
    assert "datetime.now(" not in src and "utcnow(" not in src and "time.time(" not in src
