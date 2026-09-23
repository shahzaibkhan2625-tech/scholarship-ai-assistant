"""Unit tests for the Qdrant client wrapper. All Qdrant calls are mocked --
these never touch a real cluster (see backend/tests/conftest.py's convention
of skipping rather than faking for real external dependencies; Qdrant has no
local equivalent to the real dev DB, so it's mocked instead)."""

from unittest.mock import MagicMock, patch

from qdrant_client.models import PayloadSchemaType

from app.data.vectors.qdrant_client import (
    SCHOLARSHIP_CHUNKS_COLLECTION,
    SCHOLARSHIP_ID_PAYLOAD_FIELD,
    ensure_collection,
    search_chunks,
)


def _fake_client(*, collection_exists: bool, payload_schema: dict) -> MagicMock:
    client = MagicMock()
    client.collection_exists.return_value = collection_exists
    client.get_collection.return_value = MagicMock(payload_schema=payload_schema)
    return client


def test_ensure_collection_creates_index_on_new_collection():
    """Fresh collection: create_collection then create_payload_index, once."""
    client = _fake_client(collection_exists=False, payload_schema={})

    with patch("app.data.vectors.qdrant_client.get_qdrant_client", return_value=client):
        ensure_collection()

    client.create_collection.assert_called_once()
    client.create_payload_index.assert_called_once_with(
        collection_name=SCHOLARSHIP_CHUNKS_COLLECTION,
        field_name=SCHOLARSHIP_ID_PAYLOAD_FIELD,
        field_schema=PayloadSchemaType.KEYWORD,
    )


def test_ensure_collection_self_heals_existing_collection_missing_index():
    """Regression test for Bug 2: a collection created before the index
    existed (client.collection_exists() is True, payload_schema lacks the
    field) must still get the index added -- this is exactly the live
    collection's state before the fix."""
    client = _fake_client(collection_exists=True, payload_schema={})

    with patch("app.data.vectors.qdrant_client.get_qdrant_client", return_value=client):
        ensure_collection()

    client.create_collection.assert_not_called()
    client.create_payload_index.assert_called_once_with(
        collection_name=SCHOLARSHIP_CHUNKS_COLLECTION,
        field_name=SCHOLARSHIP_ID_PAYLOAD_FIELD,
        field_schema=PayloadSchemaType.KEYWORD,
    )


def test_ensure_collection_is_idempotent_when_index_already_present():
    """No redundant create_payload_index call once the index exists."""
    client = _fake_client(
        collection_exists=True, payload_schema={SCHOLARSHIP_ID_PAYLOAD_FIELD: MagicMock()}
    )

    with patch("app.data.vectors.qdrant_client.get_qdrant_client", return_value=client):
        ensure_collection()

    client.create_payload_index.assert_not_called()


def test_search_chunks_filters_by_scholarship_id_without_raising():
    """Guards against Bug 2's failure mode: search_chunks's scholarship_id
    filter must not throw against a collection that has the index (this is
    what a real 400 would previously surface as, without the fix above)."""
    client = _fake_client(collection_exists=True, payload_schema={SCHOLARSHIP_ID_PAYLOAD_FIELD: MagicMock()})
    fake_point = MagicMock(score=0.9, payload={"text": "chunk text", "scholarship_id": "abc-123"})
    client.query_points.return_value = MagicMock(points=[fake_point])

    with patch("app.data.vectors.qdrant_client.get_qdrant_client", return_value=client):
        results = search_chunks(query_vector=[0.1, 0.2], scholarship_id="abc-123")

    assert results == [{"score": 0.9, "text": "chunk text", "scholarship_id": "abc-123"}]
    filter_kwarg = client.query_points.call_args.kwargs["query_filter"]
    assert filter_kwarg.must[0].key == "scholarship_id"
    assert filter_kwarg.must[0].match.value == "abc-123"
