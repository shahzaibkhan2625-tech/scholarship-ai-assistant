"""Repo secret-pattern scan (T222, NFR-HARDEN-8, SC-010).

Walks the repository with pathlib only (no git, so it works in any checkout) and fails on
obvious secret shapes. Allowed, in order of preference:
- `.env.example` placeholders (that file is the documented home for them);
- a line carrying the marker `allow-secret` (throwaway test-fixture credentials);
- the well-known CI service-container DSN (`postgres:postgres@localhost`).
The scanner is itself tested against a planted fake key, so it cannot pass vacuously.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

ALLOW_MARKER = "allow-secret"
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
SCAN_SUFFIXES = {".py", ".md", ".yml", ".yaml", ".toml", ".json", ".sh", ".cfg", ".ini", ".example", ".env"}
SCAN_NAMES = {"Caddyfile", "Dockerfile"}
MAX_BYTES = 1_000_000

# (label, pattern). Patterns are deliberately narrow: a false positive is cheap to mark,
# a false negative is the failure this test exists to prevent.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("OpenAI-style secret key", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("credential-bearing DSN", re.compile(r"[a-z][a-z0-9+.\-]*://[^/\s:@'\"]+:(?!\$|<|\{|\*)[^/\s@'\"]{3,}@[^\s'\"]+")),
    (
        "secret literal",
        re.compile(r"""(?i)\b\w*(?:password|passwd|secret|api_?key|token)\w*\s*[=:]\s*["'][^"'\s]{8,}["']"""),
    ),
    # Google App Password: 16 lowercase letters shown as four groups of four (spaces optional). Bare
    # 16-letter words are common in code, so it needs a password-like name in front, or the spaced
    # form in quotes.
    (
        "Google App Password",
        re.compile(
            r"""\b\w*(?i:pass|pwd|smtp)\w*\s*[=:]\s*["']?[a-z]{4}(?: ?[a-z]{4}){3}["']?(?![A-Za-z0-9_])"""
            r"""|["'][a-z]{4}(?: [a-z]{4}){3}["']"""
        ),
    ),
]

# DSN passwords that are obviously placeholders, not credentials.
PLACEHOLDER_DSN_PASSWORDS = {"password", "changeme", "xxxx", "xxxxx", "pass", "pw"}
# Fixed, public, throwaway credentials of the CI Postgres service container.
ALLOWED_LINE_FRAGMENTS = ("postgres:postgres@localhost",)


# Real local env files hold real credentials by design and are gitignored (see
# `test_real_env_files_are_gitignored`); only the committed `.env.example` is scanned.
LOCAL_ENV_NAMES = {".env", ".env.local", ".env.production", ".env.development"}


def _is_scannable(path: Path) -> bool:
    if path.name in LOCAL_ENV_NAMES:
        return False
    if path.name in SCAN_NAMES or path.name.startswith(".env"):
        return True
    return path.suffix in SCAN_SUFFIXES


def _iter_files(root: Path):
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if not path.is_file() or not _is_scannable(path):
            continue
        # Root-level *.txt run logs are not source, and are not in SCAN_SUFFIXES anyway.
        if path.name == "uv.lock" or path.stat().st_size > MAX_BYTES:
            continue
        yield path


def _line_is_allowed(path: Path, line: str, label: str, match: re.Match[str]) -> bool:
    if ALLOW_MARKER in line:
        return True
    if any(fragment in line for fragment in ALLOWED_LINE_FRAGMENTS):
        return True
    if path.name == ".env.example":
        return True
    if label == "credential-bearing DSN":
        password = match.group(0).split("://", 1)[1].split("@", 1)[0].split(":", 1)[1]
        return password.lower() in PLACEHOLDER_DSN_PASSWORDS
    return False


def scan(root: Path) -> list[str]:
    findings: list[str] = []
    for path in _iter_files(root):
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, start=1):
            for label, pattern in PATTERNS:
                for match in pattern.finditer(line):
                    if not _line_is_allowed(path, line, label, match):
                        findings.append(f"{path.relative_to(root).as_posix()}:{number}: {label}")
    return findings


def test_repo_contains_no_obvious_secrets():
    findings = scan(REPO_ROOT)
    assert not findings, "possible secrets in source control (mark deliberate fixtures with 'allow-secret'):\n" + "\n".join(findings)


def test_real_env_files_are_gitignored():
    ignored = {line.strip() for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()}
    assert ".env" in ignored, ".env must be listed in .gitignore so real credentials never reach source control"


def test_scanner_catches_planted_secrets(tmp_path):
    (tmp_path / "leak.py").write_text(
        'API_KEY = "AIza' + "A" * 35 + '"\n'
        'db = "postgresql://admin:Sup3rS3cretPw@db.example.com/app"\n'  # allow-secret: planted fake
        'password = "hunter2hunter2"\n'  # allow-secret: planted fake
        "-----BEGIN RSA PRIVATE KEY-----\n"  # allow-secret: planted fake
    )
    (tmp_path / "ok.py").write_text(
        'password = "s3cret-pass"  # allow-secret: throwaway fixture\n'
        'DATABASE_URL = "postgresql://user:password@host/dbname"\n'
        "token = os.environ['TOKEN']\n"
    )

    findings = scan(tmp_path)
    flagged = {entry.split(":")[0] for entry in findings}

    assert flagged == {"leak.py"}
    labels = " ".join(findings)
    for expected in ("Google API key", "credential-bearing DSN", "secret literal", "private key block"):
        assert expected in labels


MAIL_ENV_KEYS = ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "ALERTS_FROM_EMAIL")


def test_env_example_mail_values_are_empty_placeholders():
    """FR-MAIL-3: credentials come only from the server environment; the sample file ships blanks."""
    values = {}
    for line in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() in MAIL_ENV_KEYS:
            values[key.strip()] = value.strip()
    assert set(values) == set(MAIL_ENV_KEYS), "every mail setting must be listed in .env.example"
    assert {key: value for key, value in values.items() if value} == {}


def test_scanner_catches_an_app_password_in_every_written_form(tmp_path):
    (tmp_path / "mail.py").write_text(
        'SMTP_PASSWORD = "abcd efgh ijkl mnop"\n'  # allow-secret: planted fake
        "SMTP_PASSWORD=abcdefghijklmnop\n"  # allow-secret: planted fake
        "app_password: wxyz wxyz wxyz wxyz\n"  # allow-secret: planted fake
        'note = "abcd efgh ijkl mnop"\n'  # allow-secret: planted fake
    )
    (tmp_path / "ok.py").write_text(
        "SMTP_PASSWORD=\n"
        "smtp_password = settings.smtp_password.replace(' ', '')\n"
        'text = "this line has four short words"\n'
        "smtp_host = deterministicallyx\n"
    )

    findings = scan(tmp_path)

    assert {entry.split(":")[0] for entry in findings} == {"mail.py"}
    assert sorted(int(entry.split(":")[1]) for entry in findings) == [1, 2, 3, 4]
    assert all("Google App Password" in entry for entry in findings)


def test_scanner_skips_virtualenv_and_git_directories(tmp_path):
    for skipped in (".venv", ".git", "node_modules"):
        (tmp_path / skipped).mkdir()
        (tmp_path / skipped / "x.py").write_text('password = "hunter2hunter2"\n')  # allow-secret: planted fake

    assert scan(tmp_path) == []
