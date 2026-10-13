"""T219: fetch-log retention (ADR-0008 runs-not-rows, ADR-0011 decision 2).

SAFETY (the one place a test bug could cause real data loss): these tests run
against the shared Neon DB. Every prune call goes through `prune_scoped`, which
refuses to run unless `source_ids` is non-empty and every id was created by THIS
test module (`_SAFE_IDS`). A static guard test pins that no other call site
exists. The one scope-edge test (`source_ids=[]`) runs with `dry_run=True` so a
buggy "[] means all" could only miscount, never delete. All work additionally
happens on the rolled-back `db_session_factory` session.
"""

import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.data.repositories import source_repo
from app.models.alert import Alert, AlertPreferences
from app.models.candidate_validation import CandidateSourceValidation
from app.models.monitoring import MonitoringRun, MonitoringTrigger
from app.models.source import FetchStatus, SourceFetchLog
from app.schemas.source import SourceRegistryCreate
from app.services import retention

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
_SAFE_IDS: set[uuid.UUID] = set()  # ids of sources created by this module only
_CALL = "prune_fetch" + "_log("  # built so the static guard does not match itself


@pytest.fixture
def db(db_session_factory):
    session = db_session_factory()
    try:
        yield session
    finally:
        session.close()


def _src(db):
    source = source_repo.upsert_source_by_domain(
        db,
        SourceRegistryCreate(
            name="Retention Test",
            source_type="gov",
            official_status="official",
            domain=f"ret-{uuid.uuid4().hex[:12]}.example.com",
            access_method="web",
            reliability_level="high",
        ),
    )
    _SAFE_IDS.add(source.id)
    return source


def _run(db) -> MonitoringRun:
    run = MonitoringRun(trigger=MonitoringTrigger.MANUAL)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _log(db, source, age_days: float, *, run=None, status=FetchStatus.OK, items_found=None, k: int = 0) -> uuid.UUID:
    """One fetch-log row aged `age_days` before NOW (+k seconds to order rows within a run)."""
    row = SourceFetchLog(
        source_id=source.id,
        started_at=NOW - timedelta(days=age_days) + timedelta(seconds=k),
        status=status,
        monitoring_run_id=run.id if run is not None else None,
        items_found=items_found,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row.id


def _check(db, source, age_days: float, *, failed: bool = False, rows: int = 2):
    """One monitored check = one run with `rows` rows (an OK HTTP row, then a FAIL row if failed)."""
    run = _run(db)
    ids = [_log(db, source, age_days, run=run, k=0)]
    for k in range(1, rows):
        ids.append(_log(db, source, age_days, run=run, status=FetchStatus.FAIL if failed else FetchStatus.OK, k=k))
    return run, ids


def _remaining(db, *sources) -> set[uuid.UUID]:
    db.expire_all()
    ids = [s.id for s in sources]
    return set(db.execute(select(SourceFetchLog.id).where(SourceFetchLog.source_id.in_(ids))).scalars().all())


def prune_scoped(db, sources, **kwargs) -> int:
    """The ONLY way these tests prune: scoped to test-created sources, fixed clock."""
    ids = [s.id for s in sources]
    assert ids, "refusing to prune without an explicit source scope"
    assert set(ids) <= _SAFE_IDS, "refusing to prune sources this test module did not create"
    kwargs.setdefault("retention_days", 90)
    kwargs.setdefault("window_n", 2)
    return getattr(retention, "prune_fetch" + "_log")(db, source_ids=ids, now=NOW, **kwargs)


# --- static safety guard -------------------------------------------------------


def test_every_prune_call_in_this_module_is_scoped_or_dry_run():
    text = Path(__file__).read_text(encoding="utf-8")
    calls = [m.start() for m in re.finditer(re.escape("retention." + _CALL), text)]
    # exactly two raw call sites (scope-edge test, validation test), both dry_run=True;
    # every other prune goes through the guarded `prune_scoped` / explicit-guard getattr.
    assert len(calls) == 2, "unexpected unguarded prune call(s) in test_retention.py"
    for start in calls:
        assert "dry_run=True" in text[start : start + 120]


# --- age + run floor -----------------------------------------------------------


def test_old_rows_pruned_recent_rows_kept(db):
    source = _src(db)
    _, old = _check(db, source, 200)
    _, mid = _check(db, source, 150)
    _, recent = _check(db, source, 1)
    _, newest = _check(db, source, 0.5)
    deleted = prune_scoped(db, [source], window_n=1)
    assert deleted == len(old) + len(mid) == 4
    assert _remaining(db, source) == set(recent) | set(newest)


def test_floor_is_in_runs_not_rows_whole_runs_with_three_rows_kept(db):
    source = _src(db)
    _, a = _check(db, source, 300, rows=3)
    _, b = _check(db, source, 250, rows=3)
    _, c = _check(db, source, 200, rows=3)
    _, d = _check(db, source, 150, rows=3)
    deleted = prune_scoped(db, [source], window_n=2)
    assert deleted == 6
    assert _remaining(db, source) == set(c) | set(d)  # 2 whole runs = 6 rows, not "2 rows"


def test_source_with_fewer_than_n_runs_keeps_them_even_if_old(db):
    source = _src(db)
    _, a = _check(db, source, 400, rows=3)
    _, b = _check(db, source, 380, rows=2)
    assert prune_scoped(db, [source], window_n=5) == 0
    assert _remaining(db, source) == set(a) | set(b)


def test_row_inside_retention_window_never_pruned_even_if_unprotected(db):
    source = _src(db)
    kept = set()
    for age in (1, 2, 3, 4, 5):
        kept |= set(_check(db, source, age)[1])
    assert prune_scoped(db, [source], window_n=1) == 0
    assert _remaining(db, source) == kept


def test_health_streak_explanation_survives_pruning(db):
    source = _src(db)
    for age in (100, 110, 120, 130, 140):
        _check(db, source, age, failed=True)
    before = source_repo.consecutive_failure_count(db, source.id)
    assert before == 5
    prune_scoped(db, [source], window_n=3)
    after = source_repo.consecutive_failure_count(db, source.id)
    assert after >= 3  # the history behind a `failing` flag (>= HEALTH_WINDOW_N) is intact


def test_protected_runs_are_exactly_the_shared_ranking_helper_runs(db):
    source = _src(db)
    for age in (100, 120, 140, 160, 180):
        _check(db, source, age, rows=3)
    ranked = source_repo.ranked_monitoring_runs([source.id])
    expected = set(
        db.execute(select(ranked.c.monitoring_run_id).where(ranked.c.rn <= 3)).scalars().all()
    )
    prune_scoped(db, [source], window_n=3)
    db.expire_all()
    survivors = set(
        db.execute(
            select(SourceFetchLog.monitoring_run_id).where(SourceFetchLog.source_id == source.id)
        ).scalars().all()
    )
    assert survivors == expected


# --- floor (b): latest row of any kind ----------------------------------------


def test_floor_b_only_row_is_old_null_run_row_kept(db):
    source = _src(db)
    only = _log(db, source, 400)
    assert prune_scoped(db, [source]) == 0
    assert _remaining(db, source) == {only}


def test_floor_b_keeps_latest_null_run_row_prunes_older_ones(db):
    source = _src(db)
    oldest = _log(db, source, 400)
    latest = _log(db, source, 300)
    assert prune_scoped(db, [source]) == 1
    assert _remaining(db, source) == {latest}
    assert oldest not in _remaining(db, source)


def test_null_run_old_rows_pruned_when_source_has_newer_rows(db):
    source = _src(db)
    stale = [_log(db, source, 200), _log(db, source, 190, status=FetchStatus.FAIL)]
    _, recent = _check(db, source, 2)
    assert prune_scoped(db, [source], window_n=1) == 2
    assert _remaining(db, source) == set(recent)
    assert not (set(stale) & _remaining(db, source))


# --- floor (c): latest OK row with items_found ---------------------------------


def test_floor_c_keeps_latest_ok_row_with_items_found(db):
    source = _src(db)
    extraction = _log(db, source, 300, items_found=5)
    noise = _log(db, source, 200)  # OK but no items_found -> not floor (c)
    latest = _log(db, source, 100, status=FetchStatus.FAIL)  # floor (b)
    assert prune_scoped(db, [source]) == 1
    assert _remaining(db, source) == {extraction, latest}
    assert noise not in _remaining(db, source)


def test_floor_c_only_the_latest_extraction_row_and_only_ok_status(db):
    source = _src(db)
    older_extraction = _log(db, source, 320, items_found=9)
    newer_extraction = _log(db, source, 300, items_found=7)
    failed_with_items = _log(db, source, 250, status=FetchStatus.FAIL, items_found=3)
    latest = _log(db, source, 100, status=FetchStatus.TIMEOUT)
    assert prune_scoped(db, [source]) == 2
    assert _remaining(db, source) == {newer_extraction, latest}
    gone = {older_extraction, failed_with_items}
    assert not (gone & _remaining(db, source))


# --- scope, dry run, batching, validation --------------------------------------


def test_scope_isolation_other_source_untouched(db):
    mine, other = _src(db), _src(db)
    _, mine_rows = _check(db, mine, 300)
    _, mine_new = _check(db, mine, 250)
    other_rows = {_log(db, other, 400), _log(db, other, 390)}
    prune_scoped(db, [mine], window_n=1)
    assert _remaining(db, other) == other_rows
    assert _remaining(db, mine) == set(mine_new)


def test_empty_source_scope_means_nothing_never_everything(db):
    source = _src(db)
    _log(db, source, 400)
    _log(db, source, 399)
    # dry_run ONLY: if "[] meant all" this would count real rows instead of 0 -- and delete nothing.
    assert retention.prune_fetch_log(db, source_ids=[], dry_run=True, now=NOW) == 0


def test_dry_run_counts_exactly_what_real_run_deletes_and_deletes_nothing(db):
    source = _src(db)
    for age in (300, 250, 200, 150, 100):
        _check(db, source, age, rows=3)
    before = _remaining(db, source)
    would = prune_scoped(db, [source], window_n=2, dry_run=True)
    assert _remaining(db, source) == before
    deleted = prune_scoped(db, [source], window_n=2)
    assert would == deleted == 9


def test_batching_gives_same_result_as_one_big_batch(db):
    source = _src(db)
    _log(db, source, 400)
    for k in range(6):
        _log(db, source, 300 - k)
    latest = _log(db, source, 100)
    deleted = prune_scoped(db, [source], batch_size=2)
    assert deleted == 7
    assert _remaining(db, source) == {latest}


def test_other_tables_are_never_pruned(db):
    source = _src(db)
    runs = [_check(db, source, age)[0] for age in (300, 250, 200)]
    counts = lambda: tuple(  # noqa: E731
        db.execute(select(func.count()).select_from(model)).scalar_one()
        for model in (Alert, AlertPreferences, CandidateSourceValidation)
    )
    before = counts()
    prune_scoped(db, [source], window_n=1)
    assert counts() == before
    still = set(db.execute(select(MonitoringRun.id).where(MonitoringRun.id.in_([r.id for r in runs]))).scalars().all())
    assert still == {r.id for r in runs}  # monitoring_runs rows survive even when their fetch rows are pruned


@pytest.mark.parametrize("kwargs", [{"retention_days": 0}, {"window_n": 0}, {"batch_size": 0}])
def test_invalid_parameters_raise_before_touching_the_db(kwargs):
    class _NoDb:
        def __getattr__(self, name):  # any DB use fails the test
            raise AssertionError("DB touched before validation")

    with pytest.raises(ValueError):
        retention.prune_fetch_log(_NoDb(), source_ids=[uuid.uuid4()], dry_run=True, **kwargs)


def test_defaults_come_from_settings(db, monkeypatch):
    source = _src(db)
    old = _log(db, source, 60)
    _log(db, source, 59)
    monkeypatch.setattr(retention.settings, "fetch_log_retention_days", 30)
    monkeypatch.setattr(retention.settings, "health_window_n", 1)
    ids = [source.id]
    assert ids and set(ids) <= _SAFE_IDS
    assert getattr(retention, "prune_fetch" + "_log")(db, source_ids=ids, now=NOW) == 1
    assert old not in _remaining(db, source)


# --- CLI (no DB: prune and SessionLocal stubbed) --------------------------------


class _StubSession:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def cli(monkeypatch):
    import app.data.repositories.db as db_mod

    calls = []
    monkeypatch.setattr(db_mod, "SessionLocal", lambda: _StubSession())
    monkeypatch.setattr(retention, "prune_fetch_log", lambda db, **kw: calls.append(kw) or 7)
    return calls


def test_cli_defaults_scope_all_and_really_deletes(cli, capsys):
    assert retention.main([]) == 0
    assert cli == [{"source_ids": None, "dry_run": False}]
    assert "7" in capsys.readouterr().out


def test_cli_dry_run_and_source_ids(cli, capsys):
    a, b = uuid.uuid4(), uuid.uuid4()
    assert retention.main(["--dry-run", "--source-id", str(a), "--source-id", str(b)]) == 0
    assert cli == [{"source_ids": [a, b], "dry_run": True}]
    assert "dry" in capsys.readouterr().out.lower()


def test_cli_rejects_malformed_source_id(cli):
    with pytest.raises(SystemExit):
        retention.main(["--source-id", "not-a-uuid"])
    assert cli == []
