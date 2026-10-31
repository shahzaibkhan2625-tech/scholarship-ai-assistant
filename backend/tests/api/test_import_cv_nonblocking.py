"""010 T393: a slow CV import must not stall other requests (FR-UPLOAD-3)."""

import asyncio
import time
import uuid
from unittest.mock import patch

import httpx

from app.api.deps import get_current_user
from app.main import app
from app.schemas.profile import ProfileUpdateRequest

DELAY = 2.0


def _slow_extractor(text: str) -> ProfileUpdateRequest:
    time.sleep(DELAY)  # a blocking call, like the real LLM client
    return ProfileUpdateRequest(nationality="Pakistani")


async def _run() -> tuple[float, int, int]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        upload = asyncio.create_task(
            client.post("/profile/import-cv", files={"file": ("cv.txt", b"John Doe, Pakistani", "text/plain")})
        )
        wanted_at = time.perf_counter() + 0.5  # when /health should be sent, once the import has started
        await asyncio.sleep(0.5)
        # A blocked event loop delays this very wake-up, so measure from when /health WAS due to be
        # sent, not from when the loop finally ran it.
        health = await client.get("/health")
        health_seconds = time.perf_counter() - wanted_at
        imported = await upload
    return health_seconds, health.status_code, imported.status_code


def test_health_answers_quickly_while_a_slow_cv_import_runs():
    app.dependency_overrides[get_current_user] = lambda: type("U", (), {"id": uuid.uuid4()})()
    try:
        with patch("app.api.profile.extract_profile_from_text", _slow_extractor):
            health_seconds, health_status, import_status = asyncio.run(_run())
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert import_status == 200
    assert health_status == 200
    print(f"health answered in {health_seconds:.3f}s while the import was running")
    assert health_seconds < 1.0, f"/health took {health_seconds:.2f}s while the import ran"
