"""Unit tests of `scripts/post_deploy_check.py` (010 US9, T431; FR-DEPLOY-2, SC-009).

The script is driven against a fake HTTP site (`httpx.MockTransport`) and fake SQL callables, so
nothing here touches a network or a database. Everything the script needs comes from environment
variables, and nothing secret may reach its output.
"""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "post_deploy_check.py"

SITE = "https://site.example.test"
CHECK_EMAIL = "check-account@example.test"
CHECK_PASSWORD = "check-pw-0123456789"  # allow-secret: fake value used only by these tests
OPERATOR_TOKEN = "fake-operator-token-abcdef"  # allow-secret: fake value used only by these tests
CHECK_TOKEN = "fake-check-token-123456"  # allow-secret: fake value used only by these tests
APP_URL = "postgresql://app-user:app-pw-xyz@app-host.example.test/db"  # allow-secret: fake value
OWNER_URL = "postgresql://owner-user:owner-pw-xyz@owner-host.example.test/db"  # allow-secret: fake value

ENV = {
    "SITE_URL": SITE,
    "OPERATOR_TOKEN": OPERATOR_TOKEN,
    "CHECK_ACCOUNT_EMAIL": CHECK_EMAIL,
    "CHECK_ACCOUNT_PASSWORD": CHECK_PASSWORD,
    "APP_DATABASE_URL": APP_URL,
    "OWNER_DATABASE_URL": OWNER_URL,
}


def _load():
    spec = importlib.util.spec_from_file_location("post_deploy_check", SCRIPT)
    assert SCRIPT.exists(), f"{SCRIPT} does not exist yet"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeSite:
    """A tiny stand-in for the deployed API. Behaviour knobs are plain attributes."""

    def __init__(self):
        self.health = 200
        self.metrics = 404
        self.weak_password_status = 422
        self.account_exists = False
        self.run_status_for_check_account = 403
        self.run_status_for_operator = 202
        self.detail_status_for_check_account = 403
        self.list_status = 200
        self.match_status = 422
        self.match_body = {"url": "x", "error": "blocked: non-public address 169.254.169.254", "reported_as": "fetch_failure"}
        self.signup_calls: list[tuple[str, int]] = []  # (email, status returned)
        self.login_calls = 0
        self.seen: list[tuple[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.seen.append((request.method, path))
        auth = request.headers.get("authorization", "")
        is_operator = auth == f"Bearer {OPERATOR_TOKEN}"
        if path == "/health":
            return httpx.Response(self.health)
        if path == "/metrics":
            return httpx.Response(self.metrics)
        if path == "/auth/signup":
            body = json.loads(request.content)
            if len(body["password"]) < 8:
                status = self.weak_password_status
            elif self.account_exists:
                status = 409
            else:
                status = 201
                self.account_exists = True
            self.signup_calls.append((body["email"], status))
            return httpx.Response(status, json={"detail": "x"})
        if path == "/auth/login":
            self.login_calls += 1
            if not self.account_exists:
                return httpx.Response(401, json={"detail": "Incorrect email or password"})
            return httpx.Response(200, json={"access_token": CHECK_TOKEN, "token_type": "bearer"})
        if path == "/monitoring/runs" and request.method == "POST":
            return httpx.Response(self.run_status_for_operator if is_operator else self.run_status_for_check_account)
        if path == "/monitoring/runs" and request.method == "GET":
            return httpx.Response(self.list_status, json=[])
        if path.startswith("/monitoring/runs/"):
            return httpx.Response(200 if is_operator else self.detail_status_for_check_account)
        if path == "/scholarships/match-url":
            return httpx.Response(self.match_status, json=self.match_body)
        return httpx.Response(404)


class FakeSql:
    def __init__(self, create_table="refused", operators=1):
        self.create_table = create_table  # "refused" | "created" | "error"
        self.operators = operators
        self.urls: list[str] = []

    def app_role_create_table(self, url):
        self.urls.append(url)
        if self.create_table == "error":
            raise ConnectionError("could not connect to app-host.example.test as app-user")
        return self.create_table

    def operator_count(self, url):
        self.urls.append(url)
        return self.operators


@pytest.fixture
def pdc():
    return _load()


def run(pdc, site=None, sql=None, env=None):
    site = site or FakeSite()
    sql = sql or FakeSql()
    client = httpx.Client(transport=httpx.MockTransport(site.handler), base_url=SITE)
    results = pdc.run_checks(env or ENV, client, sql)
    return results, site, sql


def by_number(results):
    return {r.number: r for r in results}


def test_all_good_gives_pass_on_checks_1_to_6_and_8_and_manual_on_7(pdc):
    results, _, _ = run(pdc)
    statuses = {r.number: r.status for r in results}
    assert statuses == {1: "PASS", 2: "PASS", 3: "PASS", 4: "PASS", 5: "PASS", 6: "PASS", 7: "MANUAL", 8: "PASS"}
    assert not any(r.status == "FAIL" for r in results)


def test_first_run_signs_up_then_a_later_run_logs_in_with_the_same_single_account(pdc):
    site = FakeSite()
    run(pdc, site=site)  # first run: 201
    run(pdc, site=site)  # later run: 409 then login
    real = [(e, s) for e, s in site.signup_calls if e == CHECK_EMAIL and s != 422]
    assert real == [(CHECK_EMAIL, 201), (CHECK_EMAIL, 409)]
    assert {email for email, _ in site.signup_calls} == {CHECK_EMAIL}, "no other account may be created"
    assert site.login_calls == 2


@pytest.mark.parametrize(
    ("knob", "value", "number", "reason_part"),
    [
        ("health", 500, 1, "/health returned 500"),
        ("weak_password_status", 201, 2, "7-character password"),
        ("run_status_for_check_account", 202, 3, "POST /monitoring/runs"),
        ("detail_status_for_check_account", 200, 3, "GET /monitoring/runs/"),
        ("run_status_for_operator", 403, 3, "operator"),
        ("list_status", 500, 3, "list"),
        ("match_status", 200, 4, "expected 422"),
        ("metrics", 200, 6, "/metrics returned 200"),
    ],
)
def test_each_http_check_fails_with_an_exact_reason(pdc, knob, value, number, reason_part):
    site = FakeSite()
    setattr(site, knob, value)
    results, _, _ = run(pdc, site=site)
    result = by_number(results)[number]
    assert result.status == "FAIL"
    assert reason_part in result.reason


def test_match_url_check_needs_the_word_blocked_in_the_error(pdc):
    site = FakeSite()
    site.match_body = {"url": "x", "error": "timeout", "reported_as": "fetch_failure"}
    result = by_number(run(pdc, site=site)[0])[4]
    assert result.status == "FAIL" and "blocked" in result.reason


def test_check_5_fails_when_the_app_role_can_create_a_table(pdc):
    result = by_number(run(pdc, sql=FakeSql(create_table="created"))[0])[5]
    assert result.status == "FAIL" and "zz_check" in result.reason


def test_check_5_fails_when_the_database_cannot_be_reached_and_leaks_nothing(pdc):
    result = by_number(run(pdc, sql=FakeSql(create_table="error"))[0])[5]
    assert result.status == "FAIL"
    assert "ConnectionError" in result.reason
    assert "app-host" not in result.reason and "app-user" not in result.reason


@pytest.mark.parametrize("count", [0, 2])
def test_check_8_fails_on_zero_operators_and_on_more_than_one(pdc, count):
    result = by_number(run(pdc, sql=FakeSql(operators=count))[0])[8]
    assert result.status == "FAIL"
    assert str(count) in result.reason


def test_check_7_is_manual_and_never_a_failure(pdc):
    result = by_number(run(pdc)[0])[7]
    assert result.status == "MANUAL"


def test_checks_use_the_right_database_url_for_each_role(pdc):
    _, _, sql = run(pdc)
    assert sql.urls == [APP_URL, OWNER_URL]


def test_a_failed_account_check_fails_the_dependent_checks_instead_of_crashing(pdc):
    site = FakeSite()
    site.weak_password_status = 201
    site.account_exists = False
    results, _, _ = run(pdc, site=site)
    assert by_number(results)[2].status == "FAIL"
    assert len(results) == 8


def test_main_exits_zero_when_nothing_failed_and_prints_one_line_per_check(pdc, capsys):
    site, sql = FakeSite(), FakeSql()
    client = httpx.Client(transport=httpx.MockTransport(site.handler), base_url=SITE)
    code = pdc.main([], env=ENV, client=client, sql=sql)
    out = capsys.readouterr().out
    assert code == 0
    lines = [line for line in out.splitlines() if line.startswith(("PASS", "FAIL", "MANUAL"))]
    assert len(lines) == 8


def test_main_exits_non_zero_when_any_check_fails(pdc, capsys):
    site = FakeSite()
    site.metrics = 200
    client = httpx.Client(transport=httpx.MockTransport(site.handler), base_url=SITE)
    code = pdc.main([], env=ENV, client=client, sql=FakeSql())
    assert code != 0
    assert "FAIL" in capsys.readouterr().out


def test_output_never_contains_a_secret_url_email_or_token(pdc, capsys):
    site = FakeSite()
    site.metrics = 200  # make sure failure paths print too
    client = httpx.Client(transport=httpx.MockTransport(site.handler), base_url=SITE)
    pdc.main([], env=ENV, client=client, sql=FakeSql(create_table="error", operators=2))
    captured = capsys.readouterr()
    text = captured.out + captured.err
    for secret in (CHECK_PASSWORD, OPERATOR_TOKEN, CHECK_TOKEN, APP_URL, OWNER_URL, "app-pw-xyz", "owner-pw-xyz",
                   "app-host", "owner-host", CHECK_EMAIL, "site.example.test"):
        assert secret not in text, secret


@pytest.mark.parametrize("name", sorted(ENV))
def test_a_missing_variable_stops_the_run_naming_only_the_variable(pdc, capsys, name):
    env = {k: v for k, v in ENV.items() if k != name}
    code = pdc.main([], env=env, client=None, sql=FakeSql())
    out = capsys.readouterr().out
    assert code != 0
    assert name in out
    for secret in ENV.values():
        assert secret not in out


def test_the_script_reads_nothing_from_argv_or_files(pdc):
    source = SCRIPT.read_text(encoding="utf-8")
    assert "sys.argv" not in source and "open(" not in source
    for name in ENV:
        assert name in source

