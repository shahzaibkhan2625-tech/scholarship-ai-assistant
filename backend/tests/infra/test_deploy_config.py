"""Static checks of the VM deployment config (T220, NFR-HARDEN-1/6, SC-007/008).

Parses `docker-compose.podman.yml` and `Caddyfile` as text/YAML only -- no Docker, no
network, no database. ADR-0011 decision 4: Caddy is the only public listener, the backend
binds loopback and trusts forwarded headers only from it, and `STORAGE_ROOT` is a named
volume. The checks are plain functions over a string/dict so they are tested against
deliberately bad input too (they cannot pass vacuously).
"""

import copy
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = REPO_ROOT / "docker-compose.podman.yml"
CADDYFILE_PATH = REPO_ROOT / "Caddyfile"


def _load_compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def _caddyfile() -> str:
    return CADDYFILE_PATH.read_text(encoding="utf-8")


def _strip_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _command_list(service: dict) -> list[str]:
    command = service.get("command")
    assert command, "backend must override `command:` (the image CMD binds 0.0.0.0)"
    return command.split() if isinstance(command, str) else [str(part) for part in command]


def _flag_value(command: list[str], flag: str) -> str | None:
    """Value of `--flag value` or `--flag=value`; "" for a bare flag; None if absent."""
    for index, part in enumerate(command):
        if part == flag:
            nxt = command[index + 1] if index + 1 < len(command) else ""
            return "" if nxt.startswith("--") or not nxt else nxt
        if part.startswith(flag + "="):
            return part.split("=", 1)[1]
    return None


def backend_binding_problems(compose: dict) -> list[str]:
    problems: list[str] = []
    backend = compose["services"]["backend"]
    command = _command_list(backend)
    if _flag_value(command, "--host") != "127.0.0.1":
        problems.append("backend must bind --host 127.0.0.1")
    if "0.0.0.0" in " ".join(command):
        problems.append("backend command must not mention 0.0.0.0")
    if _flag_value(command, "--proxy-headers") is None:
        problems.append("backend must run with --proxy-headers")
    if _flag_value(command, "--forwarded-allow-ips") != "127.0.0.1":
        problems.append("backend must trust forwarded headers only from 127.0.0.1")
    if backend.get("ports"):
        problems.append("backend must not publish ports")
    return problems


def storage_volume_problems(compose: dict) -> list[str]:
    problems: list[str] = []
    backend = compose["services"]["backend"]
    env = backend.get("environment") or {}
    if isinstance(env, list):
        env = dict(item.split("=", 1) for item in env)
    root = env.get("STORAGE_ROOT")
    if not root or not str(root).startswith("/") or str(root).startswith("/app"):
        problems.append("STORAGE_ROOT must be set in compose to an absolute path outside /app")
        return problems
    mounted = {}
    for volume in backend.get("volumes") or []:
        source, target = str(volume).split(":")[:2]
        mounted[target] = source
    source = mounted.get(str(root))
    if source is None:
        problems.append(f"no volume is mounted at STORAGE_ROOT {root}")
    elif source.startswith(("/", ".")) or source not in (compose.get("volumes") or {}):
        problems.append(f"STORAGE_ROOT must be a declared named volume, got {source!r}")
    return problems


def caddyfile_problems(text: str) -> list[str]:
    problems: list[str] = []
    body = _strip_comments(text)
    if not re.search(r"^\s*tls\s+internal\s*$", body, re.M):
        problems.append("Caddyfile must use `tls internal` (ADR-0011 Q2)")
    proxy = re.search(r"^\s*reverse_proxy\s+(\S+)", body, re.M)
    if not proxy or proxy.group(1) not in {"127.0.0.1:8000", "localhost:8000"}:
        problems.append("Caddyfile must reverse_proxy to the loopback backend on :8000")
    if not re.search(r"^\s*redir\s+https://\{host\}\{uri\}", body, re.M):
        problems.append("Caddyfile must redirect HTTP to HTTPS")
    block = re.search(r"path\s+/metrics\b", body)
    respond = re.search(r"^\s*respond\s+@\w+\s+404\b", body, re.M)
    if not block or not respond:
        problems.append("Caddyfile must answer /metrics with 404")
    elif proxy and respond.start() > proxy.start():
        problems.append("the /metrics block must come before reverse_proxy")
    limit = re.search(r"^\s*request_body\s*\{[^}]*max_size\s+11MB[^}]*\}", body, re.M | re.S)
    if not limit:
        problems.append("Caddyfile must set `request_body { max_size 11MB }` in the site block")
    elif proxy and limit.start() > proxy.start():
        problems.append("request_body must come before reverse_proxy")
    return problems


def test_caddy_service_exists_and_is_pinned_to_a_major_version():
    caddy = _load_compose()["services"]["caddy"]
    image = caddy["image"]
    assert image.endswith("caddy:2-alpine"), image
    assert "latest" not in image
    assert caddy.get("network_mode") == "host"
    assert not caddy.get("ports"), "host networking ignores ports; do not pretend otherwise"


def test_backend_binds_loopback_and_trusts_only_the_local_proxy():
    assert backend_binding_problems(_load_compose()) == []


def test_backend_still_uses_host_networking_so_caddy_can_reach_loopback():
    assert _load_compose()["services"]["backend"].get("network_mode") == "host"


def test_storage_root_is_a_named_volume():
    assert storage_volume_problems(_load_compose()) == []


def test_caddy_keeps_its_internal_ca_in_a_named_volume():
    compose = _load_compose()
    mounts = {str(v).split(":")[1]: str(v).split(":")[0] for v in compose["services"]["caddy"]["volumes"]}
    assert mounts.get("/data") in (compose.get("volumes") or {})


def test_caddy_mounts_the_caddyfile_read_only_and_selinux_labelled():
    volumes = [str(v) for v in _load_compose()["services"]["caddy"]["volumes"]]
    entry = next(v for v in volumes if "Caddyfile" in v)
    assert entry.startswith("./Caddyfile:/etc/caddy/Caddyfile:")
    options = entry.split(":")[-1].lower().split(",")
    assert "ro" in options and "z" in options


def test_caddyfile_redirects_proxies_and_hides_metrics():
    assert caddyfile_problems(_caddyfile()) == []


def test_caddyfile_caps_request_bodies_above_the_proxy():
    body = _strip_comments(_caddyfile())
    limit = re.search(r"request_body\s*\{[^}]*max_size\s+11MB", body, re.S)
    assert limit, "request_body { max_size 11MB } is missing"
    assert limit.start() < body.index("reverse_proxy")
    assert body.index('respond @metrics 404') < body.index("reverse_proxy")


def test_checker_flags_a_missing_or_misplaced_body_limit():
    text = _caddyfile()
    stripped = re.sub(r"request_body\s*\{[^}]*\}", "", text)
    assert any("request_body" in p for p in caddyfile_problems(stripped))
    block = re.search(r"request_body\s*\{[^}]*\}", text).group(0)
    moved = text.replace(block, "").replace("reverse_proxy 127.0.0.1:8000", "reverse_proxy 127.0.0.1:8000\n" + block)
    assert any("before reverse_proxy" in p for p in caddyfile_problems(moved))


def test_site_address_is_parameterised_not_hardcoded():
    body = _strip_comments(_caddyfile())
    assert "{$SITE_ADDRESS" in body
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", body.replace("127.0.0.1", ""))


def test_compose_and_caddyfile_carry_no_secret_literals():
    text = COMPOSE_PATH.read_text(encoding="utf-8") + _caddyfile()
    assert not re.search(r"(?i)(password|secret|api_?key|token)\w*\s*[=:]\s*[\"']?[^\s\"'$]{8,}", text)
    assert "JWT_SECRET_KEY" not in text and "DATABASE_URL" not in text  # these come from .env only


# -- the checkers must actually fail on bad input --------------------------------------


def test_checker_flags_public_bind_and_missing_proxy_flags():
    bad = copy.deepcopy(_load_compose())
    bad["services"]["backend"]["command"] = ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0"]
    assert len(backend_binding_problems(bad)) >= 3


def test_checker_flags_a_bind_mount_in_place_of_a_named_volume():
    bad = copy.deepcopy(_load_compose())
    bad["services"]["backend"]["volumes"] = ["./data:/data/storage"]
    assert storage_volume_problems(bad)


def test_checker_flags_metrics_block_after_the_proxy():
    bad = (
        "{$SITE_ADDRESS} {\n tls internal\n reverse_proxy 127.0.0.1:8000\n"
        " @m path /metrics\n respond @m 404\n}\nhttp://:80 {\n redir https://{host}{uri}\n}\n"
    )
    assert any("before reverse_proxy" in p for p in caddyfile_problems(bad))


def test_checker_flags_a_missing_redirect_and_missing_tls_internal():
    problems = caddyfile_problems("{$SITE_ADDRESS} {\n reverse_proxy 127.0.0.1:8000\n}\n")
    assert any("tls internal" in p for p in problems)
    assert any("redirect" in p for p in problems)


# -- 010 US9 (T430): no owner credentials in the repo, CI runs as the restricted role ----------

WORKFLOW_PATHS = [REPO_ROOT / ".github/workflows/ci.yml", REPO_ROOT / ".github/workflows/deep-ci.yml"]
DEPLOY_TEXT_PATHS = [
    COMPOSE_PATH,
    REPO_ROOT / "docker-compose.yml",
    CADDYFILE_PATH,
    REPO_ROOT / ".env.example",
    REPO_ROOT / "docs/operations-hardening.md",
    REPO_ROOT / "docs/vm-deployment-notes.md",
]
_URL_WITH_PASSWORD = re.compile(r"postgres(?:ql)?(?:\+\w+)?://(?!user:password@)[^\s:/@<>]+:[^\s@<>]+@", re.I)
_OWNER_VALUE = re.compile(r"MIGRATION_DATABASE_URL[ \t]*[=:][ \t]*[\"']?([^\s\"']*)")


def owner_credential_problems(text: str) -> list[str]:
    """A database URL with a password, or a MIGRATION_DATABASE_URL given a literal value."""
    problems: list[str] = []
    if _URL_WITH_PASSWORD.search(text):
        problems.append("a database URL with a password is written out")
    for match in _OWNER_VALUE.finditer(text):
        value = match.group(1)
        if value and not value.startswith(("$", "<", "...")):
            problems.append("MIGRATION_DATABASE_URL has a literal value")
    return problems


def workflow_role_problems(workflow: dict) -> list[str]:
    problems: list[str] = []
    for name, job in (workflow.get("jobs") or {}).items():
        env = job.get("env") or {}
        if "scholarship_app" not in str(env.get("DATABASE_URL", "")):
            problems.append(f"{name}: DATABASE_URL must use scholarship_app")
        if len(str(env.get("JWT_SECRET_KEY", ""))) < 32:
            problems.append(f"{name}: JWT_SECRET_KEY must be at least 32 characters")
    return problems


def test_compose_files_never_mention_the_migration_url_or_the_jwt_secret():
    for path in (COMPOSE_PATH, REPO_ROOT / "docker-compose.yml"):
        text = path.read_text(encoding="utf-8")
        assert "MIGRATION_DATABASE_URL" not in text, path.name
    podman = COMPOSE_PATH.read_text(encoding="utf-8")
    assert "DATABASE_URL" not in podman and "JWT_SECRET_KEY" not in podman


def test_deploy_files_and_docs_hold_no_owner_database_url_or_value():
    for path in DEPLOY_TEXT_PATHS:
        assert owner_credential_problems(path.read_text(encoding="utf-8")) == [], path.name


def test_owner_credential_checker_flags_real_looking_values_and_allows_placeholders():
    assert owner_credential_problems("MIGRATION_DATABASE_URL=postgresql://o:pw@h/db")  # allow-secret: fake value fed to the checker
    assert owner_credential_problems("psql postgresql://owner:secret@host/db")  # allow-secret: fake value fed to the checker
    assert owner_credential_problems("MIGRATION_DATABASE_URL: abc")
    assert owner_credential_problems("-e MIGRATION_DATABASE_URL\nMIGRATION_DATABASE_URL=\n") == []
    assert owner_credential_problems("DATABASE_URL=postgresql://scholarship_app:<password>@<host>/db") == []
    assert owner_credential_problems('read -rs MIGRATION_DATABASE_URL; export MIGRATION_DATABASE_URL="$X"') == []


def test_both_ci_workflows_run_as_scholarship_app_with_a_32_character_secret():
    for path in WORKFLOW_PATHS:
        assert workflow_role_problems(yaml.safe_load(path.read_text(encoding="utf-8"))) == [], path.name


def test_workflow_checker_flags_the_owner_role_and_a_short_secret():
    bad = {"jobs": {"t": {"env": {"DATABASE_URL": "postgresql://postgres:x@h/db", "JWT_SECRET_KEY": "short"}}}}  # allow-secret: fake value fed to the checker
    assert len(workflow_role_problems(bad)) == 2


def test_caddyfile_keeps_body_limit_and_metrics_404_above_the_proxy():
    assert caddyfile_problems(_caddyfile()) == []
    body = _strip_comments(_caddyfile())
    assert body.index("request_body") < body.index("reverse_proxy")
    assert body.index("respond @metrics 404") < body.index("reverse_proxy")


# -- the runbook's "Deploy 010" section keeps the quickstart/tasks order (T433, T435-T444) ------

RUNBOOK_PATH = REPO_ROOT / "docs/operations-hardening.md"
DEPLOY_010_ORDER = [
    "git pull --ff-only",
    "branch --show-current",
    "HAVING count(*) > 1",
    "cp .env .env.pre-010",
    "length($2)",
    "### Roll-out",
    r"\password",
    "build backend",
    "read -rs MIGRATION_DATABASE_URL",
    "--preserve-env=MIGRATION_DATABASE_URL",
    "alembic current",
    "re-run the role script",
    "-pooler",
    "SMTP_PASSWORD",
    "caddy validate",
    "owner-level rights",
    "signs up",
    "app.services.operator grant",
    "### Post-deploy checks",
    "post_deploy_check.py",
    "### Rollback",
]


def deploy_010_order_problems(runbook: str, order: list[str] = DEPLOY_010_ORDER) -> list[str]:
    if "## 15. Deploy 010" not in runbook:
        return ["section 'Deploy 010' is missing"]
    section = runbook.split("## 15. Deploy 010", 1)[1]
    problems, position = [], 0
    for marker in order:
        found = section.find(marker, position)
        if found < 0:
            problems.append(f"{marker!r} is missing or out of order")
        else:
            position = found
    return problems


def test_runbook_deploy_010_steps_are_in_the_quickstart_order():
    assert deploy_010_order_problems(RUNBOOK_PATH.read_text(encoding="utf-8")) == []


def test_runbook_order_checker_flags_a_swapped_step():
    text = "## 15. Deploy 010\n" + "\n".join(reversed(DEPLOY_010_ORDER))
    assert deploy_010_order_problems(text)
    assert deploy_010_order_problems("no section here")


def test_runbook_has_the_database_roles_section_and_the_vm_notes_point_to_both():
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")
    notes = (REPO_ROOT / "docs/vm-deployment-notes.md").read_text(encoding="utf-8")
    assert "## 14. Database roles" in runbook and "read -rs OWNER_DIRECT_URL" in runbook
    assert "SSRF guard is not airtight" not in runbook
    assert "Deploy 010" in notes and "Database roles" in notes
