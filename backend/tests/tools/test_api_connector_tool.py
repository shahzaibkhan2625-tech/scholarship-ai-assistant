"""`api_connector` tool tests (T076) — the connector is monkeypatched so
these exercise only the tool's output shaping, not governance (covered in
tests/sources/test_connectors.py)."""

import uuid
from datetime import datetime, timezone

from app.sources.connectors.api_connector import ApiFetchResult
from app.tools import api_connector as api_connector_tool_module


def test_api_connector_tool_shapes_success_result(monkeypatch):
    source_id = uuid.uuid4()
    fetched_at = datetime.now(timezone.utc)

    def fake_fetch_api(_db, _source_id, _query):
        return ApiFetchResult(
            source_id=source_id,
            query="masters scholarships",
            success=True,
            records=[{"name": "Test Scholarship"}],
            http_status=200,
            fetched_at=fetched_at,
        )

    monkeypatch.setattr(api_connector_tool_module, "fetch_api", fake_fetch_api)

    output = api_connector_tool_module.api_connector_tool(db=None, source_id=source_id, query="masters scholarships")

    assert output.status == "ok"
    assert output.records == [{"name": "Test Scholarship"}]
    assert output.source_id == source_id
    assert output.fetched_at == fetched_at.isoformat()


def test_api_connector_tool_shapes_failure_result(monkeypatch):
    source_id = uuid.uuid4()

    def fake_fetch_api(_db, _source_id, _query):
        return ApiFetchResult(
            source_id=source_id,
            query="masters scholarships",
            success=False,
            records=[],
            http_status=500,
            error="API unavailable",
        )

    monkeypatch.setattr(api_connector_tool_module, "fetch_api", fake_fetch_api)

    output = api_connector_tool_module.api_connector_tool(db=None, source_id=source_id, query="masters scholarships")

    assert output.status == "fail"
    assert output.records == []
    assert output.error == "API unavailable"
