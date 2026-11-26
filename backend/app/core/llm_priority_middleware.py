"""Sets the `user_facing` LLM priority class for every HTTP request (003, research R-01).

Pure ASGI, like the other middleware. It must be a plain async wrapper (not a dependency): the context
variable is set here, before the endpoint runs, and sync endpoints (which run in the thread pool with a
copy of the current context) inherit it. Background work never passes through here; the scheduler and
the monitor declare their own class.
"""

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.llm_gate import Priority, llm_priority


class LlmPriorityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        with llm_priority(Priority.USER_FACING):
            await self.app(scope, receive, send)
