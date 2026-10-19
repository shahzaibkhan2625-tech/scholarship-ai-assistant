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
