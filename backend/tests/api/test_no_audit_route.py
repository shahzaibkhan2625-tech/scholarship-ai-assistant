"""010 US6 T407: the audit log has no read path (FR-AUDIT-4). The table is written by the app and
read only by the owner with SQL; no route, for any caller, serves it."""

import pytest

GUESSES = [
    "/audit",
    "/audit/",
    "/audit-log",
    "/audit_log",
    "/audit-logs",
    "/audits",
    "/admin/audit",
    "/admin/audit-log",
    "/operator/audit",
    "/monitoring/audit",
    "/users/me/audit",
    "/auth/audit",
]


def test_no_openapi_path_mentions_audit():
    from app.main import app

    paths = [path for path in app.openapi()["paths"] if "audit" in path.lower()]
    assert paths == []


def test_no_registered_route_mentions_audit():
    from app.main import app

    names = []
    for route in app.routes:
        prefix = getattr(getattr(route, "include_context", None), "prefix", "") or ""
        for sub in getattr(getattr(route, "original_router", None), "routes", None) or [route]:
            names.append(f"{prefix}{getattr(sub, 'path', '') or ''}")
    assert [name for name in names if "audit" in name.lower()] == []


@pytest.mark.parametrize("path", GUESSES)
@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
def test_a_student_calling_a_guessable_audit_path_gets_404_or_405(client, student_headers, path, method):
    response = client.request(method, path, headers=student_headers, json={} if method != "GET" else None)
    assert response.status_code in (404, 405)
    assert "action" not in response.text and "entity" not in response.text
