"""Structured JSON logging (002 US8, T226, NFR-HARDEN-4, ADR-0011 decision 3).

Stdlib only. Every record renders as ONE line of JSON: `timestamp` (UTC, ISO-8601),
`level`, `logger`, `message`, any `extra=` fields, and `exception` when present.

`request_id`, `run_id` and `source_id` travel in a ContextVar (`log_context`), so
existing `logger.info("... %s", id)` call sites gain those fields without being
rewritten. The context is read at FORMAT time, which is the emitting thread's
context; Starlette copies the context into its threadpool workers, so sync
endpoints see the request's id. A thread started outside a request/run has none.

Never put secrets, Authorization headers, request bodies or email addresses in
log fields; log ids.
"""

import json
import logging
import sys
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any, Iterator

__all__ = [
    "JsonFormatter",
    "bind_log_context",
    "configure_logging",
    "get_log_context",
    "log_context",
    "reset_log_context",
]

_log_fields: ContextVar[dict[str, Any]] = ContextVar("log_fields", default={})

# Every attribute a plain LogRecord carries; anything else came from `extra=`.
_STANDARD_ATTRS = frozenset(
    logging.LogRecord("x", logging.INFO, "x", 0, "x", None, None).__dict__
) | {"message", "asctime", "taskName"}

_HANDLER_MARK = "_scholarship_json_handler"


def get_log_context() -> dict[str, Any]:
    return dict(_log_fields.get())


def bind_log_context(**fields: Any) -> Token:
    """Merge `fields` into the current context; pass the token to `reset_log_context`."""
    return _log_fields.set({**_log_fields.get(), **fields})


def reset_log_context(token: Token) -> None:
    _log_fields.reset(token)


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    token = bind_log_context(**fields)
    try:
        yield
    finally:
        reset_log_context(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update({key: str(value) for key, value in _log_fields.get().items()})
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value  # explicit `extra=` wins over context
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class _CurrentStderrHandler(logging.StreamHandler):
    """Resolves `sys.stderr` at emit time, so a stream captured when logging was
    configured (e.g. by test capture) can never be left closed underneath it."""

    def __init__(self) -> None:
        logging.Handler.__init__(self)

    @property
    def stream(self):  # type: ignore[override]
        return sys.stderr

    @stream.setter
    def stream(self, _value) -> None:  # StreamHandler.setStream assigns here; ignore
        pass


def configure_logging(level: int = logging.INFO) -> None:
    """Idempotent. Installs one JSON handler on the root logger and routes
    uvicorn's own loggers through it (uvicorn configures them before the app is
    imported, so this must run afterwards, i.e. from the app lifespan)."""
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, _HANDLER_MARK, False)]:
        root.removeHandler(handler)
    handler = _CurrentStderrHandler()
    handler.setFormatter(JsonFormatter())
    setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    # The request-ID middleware writes the one access line per request (with
    # request_id and no query string); uvicorn's own would be a duplicate.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
