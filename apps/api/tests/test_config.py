import os
from pathlib import Path

import pytest

# Settings are instantiated during config module import. This deliberately
# non-connectable test value lets configuration-only tests run without a local
# database while preserving a real user-provided environment variable.
os.environ.setdefault("DATABASE_URL", "postgresql://config-test@localhost:5432/bootstrap")

from config import API_DIR, DEFAULT_ENV_FILE, Settings


pytestmark = pytest.mark.no_database


def _write_test_env(env_file: Path, database_url: str) -> None:
    env_file.write_text(f"DATABASE_URL={database_url}\n", encoding="utf-8")


def test_default_env_file_is_anchored_to_api_directory():
    """The configured local file must not depend on the shell working directory."""
    assert API_DIR == Path(__file__).resolve().parent.parent
    assert DEFAULT_ENV_FILE == API_DIR / ".env"
    assert Settings.model_config["env_file"] == DEFAULT_ENV_FILE


def test_explicit_env_file_loads_identically_from_root_and_api_cwds(
    monkeypatch, tmp_path
):
    """pydantic-settings receives an absolute env file, so cwd cannot affect it."""
    env_file = tmp_path / ".env"
    expected_url = "postgresql://config-test@localhost:5432/from_file"
    _write_test_env(env_file, expected_url)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    for cwd in (API_DIR.parent.parent, API_DIR):
        monkeypatch.chdir(cwd)
        assert Settings(_env_file=env_file).database_url == expected_url


def test_database_url_environment_variable_overrides_env_file(monkeypatch, tmp_path):
    """A per-process DATABASE_URL remains higher priority than local .env."""
    env_file = tmp_path / ".env"
    _write_test_env(env_file, "postgresql://config-test@localhost:5432/from_file")
    expected_url = "postgresql://config-test@localhost:5432/from_environment"
    monkeypatch.setenv("DATABASE_URL", expected_url)

    assert Settings(_env_file=env_file).database_url == expected_url
