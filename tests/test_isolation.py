"""The guards in conftest.py: no network, and Settings independent of `.env` and the env."""

import smtplib
from pathlib import Path

import pytest
import requests

from app.config import Settings, get_settings
from tests.conftest import NetworkAccessError


def test_http_to_internet_is_blocked() -> None:
    with pytest.raises(NetworkAccessError):
        requests.get("https://consorplus.example.com/", timeout=1)


def test_smtp_is_blocked() -> None:
    with pytest.raises(NetworkAccessError):
        smtplib.SMTP("smtp.example.com", 587, timeout=1)


def test_settings_ignore_dotenv_in_working_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("APP_ENV=development\nCHATWOOT_ACCOUNT_ID=7\n")
    monkeypatch.chdir(tmp_path)

    settings = get_settings()

    assert settings.app_env == "production"
    assert settings.chatwoot_account_id is None


def test_environment_still_works_when_set_by_the_test(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")

    assert Settings().app_env == "development"
