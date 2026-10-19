"""Structured JSON logging (002 US8, T217, NFR-HARDEN-4, ADR-0011 decision 3).

Pure unit tests: no DB, no app import. `request_id` / `run_id` / `source_id`
travel in a ContextVar so existing `logger.info("... %s", id)` call sites gain
those fields without being rewritten.
"""

import contextvars
import io
import json
import logging
import sys
import uuid
from datetime import datetime, timezone

import pytest

from app.core.logging import JsonFormatter, bind_log_context, configure_logging, get_log_context, log_context, reset_log_context


@pytest.fixture
def emit():
    """Returns `emit(message, *args, **kwargs) -> list[str]` rendering through JsonFormatter."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger(f"test.json.{uuid.uuid4().hex}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.addHandler(handler)

    def _emit(message, *args, level=logging.INFO, **kwargs):
        logger.log(level, message, *args, **kwargs)
        return stream.getvalue().splitlines()

    _emit.logger = logger
    return _emit


def test_record_is_single_line_json_with_core_fields(emit):
    lines = emit("hello %s", "world")
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["message"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["logger"].startswith("test.json.")
    stamp = datetime.fromisoformat(payload["timestamp"].replace("Z", "+00:00"))
    assert stamp.tzinfo is not None
    assert abs((datetime.now(timezone.utc) - stamp).total_seconds()) < 60


def test_multiline_message_and_exception_stay_on_one_line(emit):
    try:
        raise ValueError("boom\nsecond line")
    except ValueError:
        lines = emit("failed\nwith newline", level=logging.ERROR, exc_info=True)
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["message"] == "failed\nwith newline"
    assert "ValueError" in payload["exception"] and "boom" in payload["exception"]


def test_extra_fields_are_included_and_unserializable_values_do_not_crash(emit):
    run_id = uuid.uuid4()
    when = datetime(2027, 1, 2, tzinfo=timezone.utc)
    lines = emit("event", extra={"run_id": run_id, "changes": 3, "when": when, "obj": object()})
    payload = json.loads(lines[0])
    assert payload["run_id"] == str(run_id)
    assert payload["changes"] == 3
    assert "2027-01-02" in payload["when"]
    assert isinstance(payload["obj"], str)


def test_context_fields_appear_only_while_bound(emit):
    assert "request_id" not in json.loads(emit("before")[0])
    run_id, source_id = uuid.uuid4(), uuid.uuid4()
    with log_context(request_id="req-1", run_id=run_id, source_id=source_id):
        inside = json.loads(emit("inside")[-1])
    after = json.loads(emit("after")[-1])
    assert inside["request_id"] == "req-1"
    assert inside["run_id"] == str(run_id)
    assert inside["source_id"] == str(source_id)
    for key in ("request_id", "run_id", "source_id"):
        assert key not in after


def test_context_nests_and_restores_the_outer_value(emit):
    with log_context(run_id="outer"):
        with log_context(source_id="inner"):
            assert get_log_context() == {"run_id": "outer", "source_id": "inner"}
        assert get_log_context() == {"run_id": "outer"}
    assert get_log_context() == {}


def test_bind_and_reset_token_roundtrip(emit):
    token = bind_log_context(run_id="r1")
    assert json.loads(emit("x")[-1])["run_id"] == "r1"
    reset_log_context(token)
    assert "run_id" not in json.loads(emit("y")[-1])


def test_explicit_extra_overrides_context(emit):
    with log_context(source_id="from-context"):
        payload = json.loads(emit("x", extra={"source_id": "explicit"})[-1])
    assert payload["source_id"] == "explicit"


def test_context_propagates_into_copied_contexts_like_threadpool_workers(emit):
    with log_context(request_id="req-threadpool"):
        ctx = contextvars.copy_context()
    # copied while bound -> the copy still carries the field when run later
    line = ctx.run(emit, "worker")[-1]
    assert json.loads(line)["request_id"] == "req-threadpool"


@pytest.fixture
def restore_logging():
    root = logging.getLogger()
    saved_root = (list(root.handlers), root.level)
    uvicorn = {n: (list(logging.getLogger(n).handlers), logging.getLogger(n).propagate)
               for n in ("uvicorn", "uvicorn.error", "uvicorn.access")}
    yield
    root.handlers[:] = saved_root[0]
    root.setLevel(saved_root[1])
    for name, (handlers, propagate) in uvicorn.items():
        logging.getLogger(name).handlers[:] = handlers
        logging.getLogger(name).propagate = propagate


def test_configure_logging_is_idempotent_and_installs_one_json_handler(restore_logging):
    configure_logging()
    configure_logging()
    ours = [h for h in logging.getLogger().handlers if isinstance(h.formatter, JsonFormatter)]
    assert len(ours) == 1


def test_configure_logging_routes_uvicorn_loggers_through_json(restore_logging):
    configure_logging()
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        assert logger.handlers == []
        assert logger.propagate is True


def test_configured_handler_writes_to_the_current_stderr(restore_logging, monkeypatch):
    """The handler must resolve sys.stderr at emit time: a stream captured at
    configure time can be closed later by test capture."""
    configure_logging()
    buffer = io.StringIO()
    monkeypatch.setattr(sys, "stderr", buffer)
    logging.getLogger("app.test.stderr").warning("to current stderr")
    payload = json.loads(buffer.getvalue().splitlines()[-1])
    assert payload["message"] == "to current stderr"


# Order-dependent pair (pytest keeps definition order within a file): part 1 configures
# logging WITHOUT cleaning up after itself, exactly as a test that runs the app lifespan
# does; part 2 proves the conftest autouse fixture removed it before the next test.
def test_zz_leak_guard_part1_configures_logging_and_does_not_clean_up():
    configure_logging()
    assert any(getattr(h, "_scholarship_json_handler", False) for h in logging.getLogger().handlers)


def test_zz_leak_guard_part2_the_previous_tests_logging_config_is_gone():
    root = logging.getLogger()
    assert not any(getattr(h, "_scholarship_json_handler", False) for h in root.handlers)
    assert root.level == logging.WARNING  # pytest/stdlib default
