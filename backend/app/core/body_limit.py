"""Request-body size limit for the two upload routes (010 US5, FR-UPLOAD-1).

Pure ASGI, like `rate_limit.py` and `request_id.py`. Defence in depth behind Caddy's
`request_body` cap (and the only cap when the app runs without Caddy). For `POST
/profile/import-cv` and `POST /applications/{id}/documents`:
- a declared `Content-Length` over the limit is answered 413 before the app runs;
- a body with no (or a false) `Content-Length`, such as a chunked upload, is counted as it is
  received and the request fails with 413 once the limit is crossed.
The limit is the file limit plus a multipart allowance (boundaries and field headers); the exact
file-size rule is applied by `core/uploads.py`. Other paths and non-HTTP scopes are untouched.
"""

import re

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings
from app.core.uploads import FileTooLarge

__all__ = ["BodyLimitMiddleware", "MULTIPART_ALLOWANCE_BYTES"]

MULTIPART_ALLOWANCE_BYTES = 64 * 1024
_UPLOAD_PATHS = re.compile(r"/profile/import-cv|/applications/[^/]+/documents")


class BodyLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        max_upload_bytes: int | None = None,
        allowance: int = MULTIPART_ALLOWANCE_BYTES,
    ) -> None:
        self.app = app
        file_limit = settings.max_upload_bytes if max_upload_bytes is None else max_upload_bytes
        self.limit = file_limit + allowance

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _UPLOAD_PATHS.fullmatch(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.limit:
            await self._refuse(scope, receive, send)
            return

        received = 0
        response_started = False

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    raise FileTooLarge()
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except FileTooLarge:
            # Inside FastAPI the exception handler has usually answered already; a bare ASGI app
            # (and a failure before any response) is answered here.
            if response_started:
                raise
            await self._refuse(scope, receive, send)

    @staticmethod
    async def _refuse(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse({"detail": FileTooLarge.message}, status_code=FileTooLarge.status_code_value)
        await response(scope, receive, send)
