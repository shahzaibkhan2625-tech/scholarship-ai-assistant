"""Post-deploy checks for feature 010 (US9, FR-DEPLOY-2, SC-009).

Run after the containers are recreated; see docs/operations-hardening.md "Deploy 010".
Everything comes from environment variables (nothing on the command line, nothing read from a
file) and nothing secret is printed: a failure shows the check number, a fixed description and the
HTTP status or exception class, never a URL, token, password or e-mail address.

  SITE_URL                 public address of the site, e.g. https://<site>
  OPERATOR_TOKEN           access token of the owner's operator account
  CHECK_ACCOUNT_EMAIL      the ONE fixed non-operator check account (signed up on the first run,
  CHECK_ACCOUNT_PASSWORD   logged into on later runs, so no account is created per run; 8-72 bytes)
  APP_DATABASE_URL         scholarship_app URL (the restricted role)
  OWNER_DATABASE_URL       owner URL (read-only query)
  SITE_VERIFY_TLS          optional; "0" skips certificate checks (Caddy `tls internal`)

Prints PASS, FAIL or MANUAL per check and exits non-zero if any check is FAIL (2 if a variable
is missing). Check 7 (the alert e-mail) cannot be automated and is always MANUAL.
"""

import os
import sys
import uuid
from dataclasses import dataclass

import httpx

REQUIRED = (
    "SITE_URL",
    "OPERATOR_TOKEN",
    "CHECK_ACCOUNT_EMAIL",
    "CHECK_ACCOUNT_PASSWORD",
    "APP_DATABASE_URL",
    "OWNER_DATABASE_URL",
)
TIMEOUT_SECONDS = 20.0
METADATA_URL = "http://169.254.169.254/"
WEAK_PASSWORD = "x" * 7  # one below the 8-byte minimum


@dataclass
class Result:
    number: int
    name: str
    status: str  # PASS | FAIL | MANUAL
    reason: str = ""


def _ok(number: int, name: str, reason: str = "") -> Result:
    return Result(number, name, "PASS", reason)


def _fail(number: int, name: str, reason: str) -> Result:
    return Result(number, name, "FAIL", reason)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _expect(client, method, path, number, name, want, **kwargs):
    """Send one request; return (response | None, FAIL Result | None)."""
    try:
        response = client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        return None, _fail(number, name, f"{method} {path} raised {type(exc).__name__}")
    if response.status_code != want:
        return response, _fail(number, name, f"{method} {path} returned {response.status_code}, expected {want}")
    return response, None


class PsycopgSql:
    """The two database probes. Only exception class names are ever surfaced."""

    def app_role_create_table(self, url: str) -> str:
        import psycopg

        with psycopg.connect(url, connect_timeout=15) as conn:
            try:
                conn.execute("CREATE TABLE zz_check (a int)")
            except psycopg.errors.InsufficientPrivilege:
                conn.rollback()
                return "refused"
            conn.rollback()  # DDL is transactional: nothing stays behind
            return "created"

    def operator_count(self, url: str) -> int:
        import psycopg

        with psycopg.connect(url, connect_timeout=15) as conn:
            return len(conn.execute("SELECT 1 FROM users WHERE is_operator").fetchall())


def check_health(client) -> Result:
    name = "site answers over HTTPS (/health)"
    _, failure = _expect(client, "GET", "/health", 1, name, 200)
    return failure or _ok(1, name)


def check_accounts(client, email: str, password: str) -> tuple[Result, str | None]:
    """Weak password refused; the fixed account signs up (201) or, after a 409, logs in."""
    name = "password rules; fixed check account signs up or logs in"
    _, failure = _expect(client, "POST", "/auth/signup", 2, name, 422, json={"email": email, "password": WEAK_PASSWORD})
    if failure:
        return _fail(2, name, f"a 7-character password was not refused: {failure.reason}"), None
    try:
        signup = client.post("/auth/signup", json={"email": email, "password": password})
    except httpx.HTTPError as exc:
        return _fail(2, name, f"POST /auth/signup raised {type(exc).__name__}"), None
    if signup.status_code not in (201, 409):
        return _fail(2, name, f"POST /auth/signup returned {signup.status_code}, expected 201 or 409"), None
    login, failure = _expect(client, "POST", "/auth/login", 2, name, 200, json={"email": email, "password": password})
    if failure:
        return failure, None
    token = login.json().get("access_token")
    if not token:
        return _fail(2, name, "POST /auth/login returned no access token"), None
    return _ok(2, name, "created" if signup.status_code == 201 else "existing account"), token


def check_operator_boundary(client, check_token: str | None, operator_token: str) -> Result:
    name = "non-operator forbidden on operator routes, operator allowed"
    if not check_token:
        return _fail(3, name, "no check-account token (check 2 failed)")
    guest = _bearer(check_token)
    _, failure = _expect(client, "POST", "/monitoring/runs", 3, name, 403, headers=guest)
    if failure:
        return failure
    _, failure = _expect(client, "GET", f"/monitoring/runs/{uuid.uuid4()}", 3, name, 403, headers=guest)
    if failure:
        return failure
    _, failure = _expect(client, "GET", "/monitoring/runs", 3, name, 200, headers=guest)
    if failure:
        return _fail(3, name, "check account " + failure.reason + " (list)")
    operator = _bearer(operator_token)
    _, failure = _expect(client, "POST", "/monitoring/runs", 3, name, 202, headers=operator)
    if failure:
        return _fail(3, name, "operator " + failure.reason)
    _, failure = _expect(client, "GET", "/monitoring/runs", 3, name, 200, headers=operator)
    if failure:
        return _fail(3, name, "operator " + failure.reason + " (list)")
    return _ok(3, name)


def check_match_url_blocked(client, check_token: str | None) -> Result:
    name = "match-url to the metadata address is refused"
    if not check_token:
        return _fail(4, name, "no check-account token (check 2 failed)")
    response, failure = _expect(
        client, "POST", "/scholarships/match-url", 4, name, 422, headers=_bearer(check_token), json={"url": METADATA_URL}
    )
    if failure:
        return failure
    try:
        error = str(response.json().get("error", ""))
    except ValueError:
        error = ""
    if "blocked" not in error:
        return _fail(4, name, "returned 422 but the error does not say blocked")
    return _ok(4, name)


def check_app_role_cannot_create(sql, url: str) -> Result:
    name = "app database role cannot create a table"
    try:
        outcome = sql.app_role_create_table(url)
    except Exception as exc:  # noqa: BLE001 - only the class name may be shown
        return _fail(5, name, f"could not run the probe: {type(exc).__name__}")
    if outcome != "refused":
        return _fail(5, name, "CREATE TABLE zz_check was not refused")
    return _ok(5, name)


def check_metrics_hidden(client) -> Result:
    name = "public /metrics is refused"
    _, failure = _expect(client, "GET", "/metrics", 6, name, 404)
    return failure or _ok(6, name)


def check_alert_email_manual() -> Result:
    return Result(7, "alert e-mail reaches the owner", "MANUAL", "trigger one alert and confirm the mail within 5 minutes")


def check_single_operator(sql, url: str) -> Result:
    name = "the owner is the only operator"
    try:
        count = sql.operator_count(url)
    except Exception as exc:  # noqa: BLE001
        return _fail(8, name, f"could not run the query: {type(exc).__name__}")
    if count != 1:
        return _fail(8, name, f"{count} operator rows, expected exactly 1")
    return _ok(8, name)


def run_checks(env, client, sql) -> list[Result]:
    account, token = check_accounts(client, env["CHECK_ACCOUNT_EMAIL"], env["CHECK_ACCOUNT_PASSWORD"])
    return [
        check_health(client),
        account,
        check_operator_boundary(client, token, env["OPERATOR_TOKEN"]),
        check_match_url_blocked(client, token),
        check_app_role_cannot_create(sql, env["APP_DATABASE_URL"]),
        check_metrics_hidden(client),
        check_alert_email_manual(),
        check_single_operator(sql, env["OWNER_DATABASE_URL"]),
    ]


def main(argv=None, env=None, client=None, sql=None) -> int:
    env = os.environ if env is None else env
    missing = [name for name in REQUIRED if not env.get(name)]
    if missing:
        print("FAIL: missing environment variables: " + ", ".join(missing))
        return 2
    if client is None:
        verify = env.get("SITE_VERIFY_TLS", "1") != "0"
        client = httpx.Client(base_url=env["SITE_URL"].rstrip("/"), timeout=TIMEOUT_SECONDS, verify=verify)
    results = run_checks(env, client, sql or PsycopgSql())
    for result in results:
        detail = f" - {result.reason}" if result.reason else ""
        print(f"{result.status} {result.number}. {result.name}{detail}")
    failed = [r.number for r in results if r.status == "FAIL"]
    print("RESULT: FAIL (checks " + ", ".join(map(str, failed)) + ")" if failed else "RESULT: no failures (check 7 is manual)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
