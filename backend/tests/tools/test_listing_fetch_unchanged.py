"""003 T281 (R-07): `listing_fetch_tool` maps the connector's new `unchanged` result to the existing
`cached` outcome (with the persisted scholarship refs) and passes `deferred` through; the connector
is stubbed, so nothing is fetched and no provider is called."""

import uuid
from datetime import datetime, timezone

from app.models.source import VerificationStatus
from app.sources.connectors.official_fetch import CachedScholarshipRef, ListingExtractionResult
from app.tools import listing_fetch as tool

SOURCE_ID = uuid.uuid4()
REFS = [
    CachedScholarshipRef(uuid.uuid4(), VerificationStatus.VERIFIED),
    CachedScholarshipRef(uuid.uuid4(), VerificationStatus.UNVERIFIED),
]


def _run(monkeypatch, result: ListingExtractionResult):
    monkeypatch.setattr(tool, "fetch_and_extract_listing", lambda db, source_id, url=None: result)
    return tool.listing_fetch_tool(db=None, source_id=SOURCE_ID)


def test_unchanged_is_reported_as_cached_with_the_persisted_refs(monkeypatch):
    out = _run(monkeypatch, ListingExtractionResult(source_id=SOURCE_ID, status="unchanged", cached=REFS))
    assert out.status == "cached" and out.error is None and out.candidates == []
    assert [c.scholarship_id for c in out.cached] == [r.scholarship_id for r in REFS]
    assert [c.verification_status for c in out.cached] == [r.verification_status for r in REFS]


def test_deferred_passes_through_with_the_persisted_refs_and_no_error(monkeypatch):
    retry_at = datetime(2026, 11, 27, tzinfo=timezone.utc)
    out = _run(monkeypatch, ListingExtractionResult(source_id=SOURCE_ID, status="deferred", cached=REFS, retry_at=retry_at))
    assert out.status == "deferred" and out.error is None and out.candidates == []
    assert len(out.cached) == 2


def test_the_existing_statuses_are_unchanged(monkeypatch):
    assert _run(monkeypatch, ListingExtractionResult(source_id=SOURCE_ID, status="cached", cached=REFS)).status == "cached"
    failed = _run(monkeypatch, ListingExtractionResult(source_id=SOURCE_ID, status="fail", error="boom"))
    assert (failed.status, failed.error) == ("fail", "boom")
