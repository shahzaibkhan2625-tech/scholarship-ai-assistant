"""010 US4: a weak signing secret stops the application at start-up (FR-CRED-3)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings

BACKEND = Path(__file__).resolve().parents[2]
WEAK = ["x" * 31, "", "change-me-to-a-random-secret"]  # allow-secret


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:pw@localhost/db")  # allow-secret
    return monkeypatch


@pytest.mark.parametrize("secret", WEAK)
def test_weak_secret_is_refused_without_echoing_it(env, secret):
    env.setenv("JWT_SECRET_KEY", secret)
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)
    message = str(caught.value)
    assert "JWT_SECRET_KEY" in message and "32" in message
    if secret:
        assert secret not in message.replace("input_value=", "")
        assert f"input_value='{secret}'" not in message


def test_32_character_secret_loads(env):
    env.setenv("JWT_SECRET_KEY", "x" * 32)
    assert Settings(_env_file=None).jwt_secret_key == "x" * 32


@pytest.mark.parametrize("secret", ["w" * 31, "change-me-to-a-random-secret"])  # allow-secret
def test_app_start_with_a_weak_secret_exits_without_printing_it(secret):
    child_env = {**os.environ, "JWT_SECRET_KEY": secret, "DATABASE_URL": "postgresql://app:pw@localhost/db"}  # allow-secret
    done = subprocess.run(
        [sys.executable, "-c", "import app.main"], cwd=BACKEND, env=child_env,
        capture_output=True, text=True, timeout=60,
    )
    assert done.returncode != 0
    assert secret not in done.stderr and secret not in done.stdout
