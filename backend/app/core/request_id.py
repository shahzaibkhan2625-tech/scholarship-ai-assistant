"""Request-ID middleware (002 US8 — an ADDITION to the task list, ADR-0011 decision 3).

Pure ASGI. Per HTTP request it:
- takes a well-formed inbound `X-Request-ID` (`[A-Za-z0-9._-]{1,64}`) or generates
  one — a malformed inbound value is replaced, never logged, so a client cannot
  inject newlines or markup into logs;
- binds it into the log context for the duration of the request (sync endpoints
  run in a threadpool that receives a copy of the context);
- echoes it on the response;
- writes ONE access line on logger `app.access`: method, path, status,
  duration_ms. Never headers, bodies or the query string (tokens can travel there).
"""

import logging
import re
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import log_context

__all__ = ["REQUEST_ID_HEADER", "RequestIDMiddleware"]

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")

access_logger = logging.getLogger("app.access")


class RequestIDMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = Headers(scope=scope).get(REQUEST_ID_HEADER)
        request_id = inbound if inbound and _VALID_ID.fullmatch(inbound) else uuid.uuid4().hex
        status_code = 500  # what the client sees if the app raises before responding
        started = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        with log_context(request_id=request_id):
            try:
                await self.app(scope, receive, send_with_id)
            finally:
                access_logger.info(
                    "request",
                    extra={
                        "method": scope.get("method"),
                        "path": scope.get("path"),
                        "status": status_code,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                        "request_id": request_id,
                    },
                )
