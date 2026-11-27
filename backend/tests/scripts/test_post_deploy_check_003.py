"""003 T464 (FR-OBS-2, FR-OBS-5, FR-SKIP-2, FR-BUDGET-2): the post-deploy checks added for feature 003.

Driven against a fake HTTP site (`httpx.MockTransport`) and fake SQL callables: no network, no database. The
checks continue the numbering of the 010 checks (1-8):

  9  the Alembic head equals the 003 revision (owner URL, read only)
  10 as `scholarship_app`, SELECT and UPDATE work on the three new tables (rolled back); CREATE TABLE stays refused
  11 `GET /monitoring/budget`: 403 for the check account, 200 for the operator
  12 a manual run, repeated: the second run does not extract again; today's `allowed` total never above the allowance
  13 MANUAL: the start-up configuration line is in the log

`GET /metrics` through Caddy still returning 404 is 010's check 6 and is not repeated."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "post_deploy_check.py"
SITE = "https://site.example.test"
OPERATOR_TOKEN = "fake-operator-token-abcdef"  # allow-secret: fake value used only by these tests
CHECK_TOKEN = "fake-check-token-123456"  # allow-secret: fake value used only by these tests
APP_URL = "postgresql://app-user:app-pw-xyz@app-host.example.test/db"  # allow-secret: fake value
OWNER_URL = "postgresql://owner-user:owner-pw-xyz@owner-host.example.test/db"  # allow-secret: fake value
REVISION = "b4e7a91c3d52"


def _load():
    spec = importlib.util.spec_from_file_location("post_deploy_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeSite:
    def __init__(self):
        self.budget_status_check_account = 403
        self.budget_status_operator = 200
        self.allowance, self.used = 20, {"user_facing": 1, "backfill": 0, "monitoring": 2, "validation": 0}
        self.runs: list[dict] = []
        self.second_run = {"status": "completed", "sources_processed": 0, "sources_skipped": 0, "sources_deferred": 0}
        self.first_run = {"status": "completed", "sources_processed": 2, "sources_skipped": 0, "sources_deferred": 0}
        self.running_polls = 0  # how many GETs answer "running" before the run completes
        self.post_status = 202
        self.seen: list[tuple[str, str]] = []
        self._polls = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.seen.append((method, path))
        is_operator = request.headers.get("authorization") == f"Bearer {OPERATOR_TOKEN}"
        if path == "/monitoring/budget":
            if not is_operator:
                return httpx.Response(self.budget_status_check_account, json={"detail": "Operator access required"})
            body = {
                "budget_day": "2026-11-27", "reset_at": "2026-11-28T08:00:00Z", "classify_pending": 0, "sources": [],
                "request_types": {"generate": {"allowance": self.allowance, "used": self.used,
                                               "remaining": self.allowance - sum(self.used.values())}},
            }
            return httpx.Response(self.budget_status_operator, json=body)
        if path == "/monitoring/runs" and method == "POST":
            run = {"id": f"run-{len(self.runs) + 1}"}
            self.runs.append(run)
            return httpx.Response(self.post_status, json={**run, "status": "running"})
        if path == "/monitoring/runs" and method == "GET":
            running = self.runs and self._polls.get(self.runs[-1]["id"], 0) < self.running_polls
            return httpx.Response(200, json=[{"id": self.runs[-1]["id"], "status": "running" if running else "completed"}] if self.runs else [])
        if path.startswith("/monitoring/runs/"):
            run_id = path.rsplit("/", 1)[1]
            polls = self._polls[run_id] = self._polls.get(run_id, 0) + 1
            if polls <= self.running_polls:
                return httpx.Response(200, json={"id": run_id, "status": "running", "sources_processed": 0})
            body = self.first_run if run_id == "run-1" else self.second_run
            return httpx.Response(200, json={"id": run_id, **body})
        return httpx.Response(404)


class FakeSql:
    def __init__(self, head=REVISION, table_problems=None):
        self.head, self.table_problems = head, table_problems or []
        self.urls: list[str] = []

    def alembic_head(self, url):
        self.urls.append(url)
        if isinstance(self.head, Exception):
            raise self.head
        return self.head

    def app_role_budget_tables(self, url):
        self.urls.append(url)
        return list(self.table_problems)


@pytest.fixture
def pdc():
    return _load()


def run(pdc, site=None, sql=None, check_token=CHECK_TOKEN):
    site, sql = site or FakeSite(), sql or FakeSql()
    client = httpx.Client(transport=httpx.MockTransport(site.handler), base_url=SITE)
    env = {"OPERATOR_TOKEN": OPERATOR_TOKEN, "APP_DATABASE_URL": APP_URL, "OWNER_DATABASE_URL": OWNER_URL}
    results = pdc.run_checks_003(env, client, sql, check_token, sleep=lambda s: None)
    return {r.number: r for r in results}, site, sql


def test_all_good_passes_checks_9_to_12_and_13_is_manual(pdc):
    results, _, _ = run(pdc)
    assert {n: r.status for n, r in results.items()} == {9: "PASS", 10: "PASS", 11: "PASS", 12: "PASS", 13: "MANUAL"}


def test_check_9_fails_on_a_different_head_and_on_an_unreachable_database(pdc):
    results, _, _ = run(pdc, sql=FakeSql(head="a807daf8460e"))
    assert results[9].status == "FAIL" and "head" in results[9].reason
    results, _, _ = run(pdc, sql=FakeSql(head=ConnectionError("could not connect to owner-host.example.test")))
    assert results[9].status == "FAIL" and "ConnectionError" in results[9].reason
    assert "owner-host" not in results[9].reason and "owner-pw" not in results[9].reason


def test_check_9_uses_the_owner_url_and_check_10_the_app_url(pdc):
    _, _, sql = run(pdc)
    assert sql.urls == [OWNER_URL, APP_URL]


def test_check_10_fails_when_the_app_role_cannot_use_a_new_table(pdc):
    results, _, _ = run(pdc, sql=FakeSql(table_problems=["llm_budget_ledger:UPDATE"]))
    assert results[10].status == "FAIL" and "llm_budget_ledger" in results[10].reason


def test_check_11_needs_403_for_the_check_account_and_200_for_the_operator(pdc):
    site = FakeSite()
    site.budget_status_check_account = 200
    assert run(pdc, site=site)[0][11].status == "FAIL"
    site = FakeSite()
    site.budget_status_operator = 403
    assert run(pdc, site=site)[0][11].status == "FAIL"
    assert run(pdc, check_token=None)[0][11].status == "FAIL"  # no check-account token (010 check 2 failed)


def test_check_12_second_run_that_checks_nothing_passes(pdc):
    results, site, _ = run(pdc)  # second run: processed 0 (nothing due), skipped 0
    assert results[12].status == "PASS"
    assert [call for call in site.seen if call == ("POST", "/monitoring/runs")] == [("POST", "/monitoring/runs")] * 2


def test_check_12_second_run_with_skipped_sources_passes(pdc):
    site = FakeSite()
    site.second_run = {"status": "completed", "sources_processed": 2, "sources_skipped": 2, "sources_deferred": 0}
    assert run(pdc, site=site)[0][12].status == "PASS"


def test_check_12_fails_when_the_second_run_extracts_again(pdc):
    site = FakeSite()
    site.second_run = {"status": "completed", "sources_processed": 2, "sources_skipped": 0, "sources_deferred": 0}
    results, _, _ = run(pdc, site=site)
    assert results[12].status == "FAIL" and "extract" in results[12].reason


def test_check_12_fails_when_the_ledger_is_above_the_allowance(pdc):
    site = FakeSite()
    site.used = {"user_facing": 15, "backfill": 0, "monitoring": 8, "validation": 0}  # 23 > 20
    results, _, _ = run(pdc, site=site)
    assert results[12].status == "FAIL" and "allowance" in results[12].reason


def test_check_12_waits_for_a_running_run_and_gives_up_after_a_timeout(pdc):
    site = FakeSite()
    site.running_polls = 3  # answers "running" three times, then completes
    assert run(pdc, site=site)[0][12].status == "PASS"
    stuck = FakeSite()
    stuck.running_polls = 10_000
    results, _, _ = run(pdc, site=stuck)
    assert results[12].status == "FAIL" and "did not finish" in results[12].reason


def test_check_12_reports_a_refused_trigger(pdc):
    site = FakeSite()
    site.post_status = 403
    results, _, _ = run(pdc, site=site)
    assert results[12].status == "FAIL" and "403" in results[12].reason


def test_output_of_the_new_checks_leaks_no_secret(pdc):
    site = FakeSite()
    site.budget_status_operator = 500
    results, _, _ = run(pdc, site=site, sql=FakeSql(head=ConnectionError(f"{APP_URL} {OWNER_URL}")))
    text = json.dumps([(r.name, r.status, r.reason) for r in results.values()])
    for secret in (OPERATOR_TOKEN, CHECK_TOKEN, APP_URL, OWNER_URL, "app-pw-xyz", "owner-pw-xyz", "site.example.test"):
        assert secret not in text, secret


def test_the_script_needs_no_new_environment_variable(pdc):
    assert set(pdc.REQUIRED) == {
        "SITE_URL", "OPERATOR_TOKEN", "CHECK_ACCOUNT_EMAIL", "CHECK_ACCOUNT_PASSWORD", "APP_DATABASE_URL",
        "OWNER_DATABASE_URL",
    }
    source = SCRIPT.read_text(encoding="utf-8")
    assert "sys.argv" not in source and "open(" not in source
