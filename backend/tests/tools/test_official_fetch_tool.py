"""`official_fetch` tool tests (T074) — the connector is monkeypatched so
these exercise only the tool's output shaping, not governance (covered in
tests/sources/test_connectors.py)."""

import uuid
from datetime import datetime, timezone

from app.sources.connectors.official_fetch import OfficialFetchResult
from app.tools import official_fetch as official_fetch_tool_module


def test_official_fetch_tool_shapes_success_result(monkeypatch):
    source_id = uuid.uuid4()
    fetched_at = datetime.now(timezone.utc)

    def fake_fetch_official(_db, _source_id, _url):
        return OfficialFetchResult(
            source_id=source_id,
            source_url="https://example.com/page",
            success=True,
            content="<html/>",
            http_status=200,
            fetched_at=fetched_at,
        )

    monkeypatch.setattr(official_fetch_tool_module, "fetch_official", fake_fetch_official)

    output = official_fetch_tool_module.official_fetch_tool(db=None, source_id=source_id, url="https://example.com/page")

    assert output.status == "ok"
    assert output.content == "<html/>"
    assert output.source_id == source_id
    assert output.fetched_at == fetched_at.isoformat()


def test_official_fetch_tool_shapes_failure_result(monkeypatch):
    source_id = uuid.uuid4()

    def fake_fetch_official(_db, _source_id, _url):
        return OfficialFetchResult(
            source_id=source_id,
            source_url="https://example.com/page",
            success=False,
            http_status=503,
            error="upstream unavailable",
        )

    monkeypatch.setattr(official_fetch_tool_module, "fetch_official", fake_fetch_official)

    output = official_fetch_tool_module.official_fetch_tool(db=None, source_id=source_id, url="https://example.com/page")

    assert output.status == "fail"
    assert output.content is None
    assert output.error == "upstream unavailable"
