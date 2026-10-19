"""T172/T176: per-source advisory lock (FR-MON-6). Transaction-scoped on
purpose (Neon pooled endpoint): released at commit/rollback, never by an
explicit unlock. Uses real, separate DB connections; skips if no DB."""

import uuid

import pytest

from app.scheduling.locks import source_lock, source_lock_key, try_acquire_source_lock


@pytest.fixture
def engine(db_session_factory):  # db_session_factory skips when the DB is unreachable
    from app.data.repositories.db import engine

    return engine


def test_lock_key_is_stable_signed_int64_and_distinct_per_source():
    a, b = uuid.uuid4(), uuid.uuid4()
    assert source_lock_key(a) == source_lock_key(a)
    assert source_lock_key(a) != source_lock_key(b)
    assert -(2**63) <= source_lock_key(a) < 2**63


def test_lock_key_does_not_use_builtin_hash():
    import ast
    import inspect

    from app.scheduling import locks

    calls = [
        node.func.id
        for node in ast.walk(ast.parse(inspect.getsource(locks)))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "hash" not in calls


@pytest.mark.parametrize("end", ["commit", "rollback"])
def test_second_connection_blocked_while_held_and_free_after_transaction_ends(engine, end):
    sid, other = uuid.uuid4(), uuid.uuid4()
    with engine.connect() as holder, engine.connect() as contender:
        holder_txn = holder.begin()
        contender_txn = contender.begin()
        assert try_acquire_source_lock(holder, sid) is True
        assert try_acquire_source_lock(contender, sid) is False  # same source: blocked, never blocks the caller
        assert try_acquire_source_lock(contender, other) is True  # different source unaffected

        getattr(holder_txn, end)()  # lock freed by transaction end, no explicit unlock call
        assert try_acquire_source_lock(contender, sid) is True
        contender_txn.rollback()


def test_source_lock_context_is_exclusive_and_releases_on_exit_and_on_exception(db_session_factory):
    session = db_session_factory()
    sid = uuid.uuid4()
    with source_lock(session, sid) as first:
        assert first is True
        with source_lock(session, sid) as second:
            assert second is False
        with source_lock(session, uuid.uuid4()) as other:
            assert other is True

    with source_lock(session, sid) as again:
        assert again is True

    with pytest.raises(RuntimeError):
        with source_lock(session, sid) as held:
            assert held is True
            raise RuntimeError("boom")
    with source_lock(session, sid) as after_error:
        assert after_error is True
    session.close()
