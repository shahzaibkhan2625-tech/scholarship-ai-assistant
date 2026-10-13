"""Prometheus metrics + `GET /metrics` (002 US8, T218, NFR-HARDEN-4, O-4, ADR-0011).

`/metrics` sits behind the existing bearer auth (O-4); any registered user can
read it (ADR-0011 Q5, accepted). Labels are low-cardinality on purpose: never a
`source_id`. Run-level counter behaviour (a monitoring run increments them) is
asserted in tests/workflows/test_source_monitor.py next to the run fixtures.
"""

import pytest
from prometheus_client.parser import text_string_to_metric_families

from app.core import metrics


def test_exposes_the_documented_metrics_with_expected_types():
    names = {family.name: family.type for family in metrics.REGISTRY.collect()}
    assert names["monitoring_runs"] == "counter"
    assert names["source_fetch"] == "counter"
    assert names["retention_rows_deleted"] == "counter"
    assert names["monitoring_run_duration_seconds"] == "histogram"


def test_labels_are_status_only_never_a_source_id():
    metrics.SOURCE_FETCH_TOTAL.labels(status="ok").inc()
    metrics.MONITORING_RUNS_TOTAL.labels(status="completed").inc()
    for family in metrics.REGISTRY.collect():
        if family.name in ("source_fetch", "monitoring_runs"):
            for sample in family.samples:
                assert set(sample.labels) == {"status"}, sample


def test_metrics_requires_a_bearer_token(client):
    assert client.get("/metrics").status_code == 401


def test_metrics_rejects_an_invalid_token(client):
    response = client.get("/metrics", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401


def test_metrics_returns_prometheus_text_with_a_valid_token(authed_user):
    metrics.RETENTION_ROWS_DELETED_TOTAL.inc(0)
    response = authed_user["client"].get("/metrics", headers=authed_user["headers"])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    families = {family.name: family for family in text_string_to_metric_families(response.text)}
    assert {"monitoring_runs", "source_fetch", "retention_rows_deleted", "monitoring_run_duration_seconds"} <= set(families)
    assert "# TYPE monitoring_runs_total counter" in response.text


def test_metrics_route_is_in_the_openapi_schema():
    from app.main import app

    assert "/metrics" in app.openapi()["paths"]
