"""On startup, the api answers what a restart left unanswered."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import get_settings


class _Bot:
    def __init__(self) -> None:
        self.calls: list[timedelta] = []

    def recover_unanswered(self, max_age: timedelta) -> None:
        self.calls.append(max_age)


def test_recovery_runs_on_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHATSAPP_RECOVERY_MINUTES", "20")
    get_settings.cache_clear()
    bot = _Bot()
    started: list[str] = []
    monkeypatch.setattr(main.whatsapp_webhook, "build_whatsapp_bot", lambda settings: bot)

    class _Thread:
        def __init__(self, target, name, daemon):  # type: ignore[no-untyped-def]
            self.target = target
            started.append(name)

        def start(self) -> None:
            self.target()

    monkeypatch.setattr(main.threading, "Thread", _Thread)

    with TestClient(main.app):
        pass

    assert bot.calls == [timedelta(minutes=20)]
    assert started == ["wa-recovery"]


def test_recovery_survives_a_missing_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(settings: object) -> None:
        raise RuntimeError("faltan WHATSAPP_ACCESS_TOKEN o WHATSAPP_PHONE_NUMBER_ID")

    monkeypatch.setattr(main.whatsapp_webhook, "build_whatsapp_bot", broken)

    main.recover_whatsapp()  # logs, never raises


def test_recovery_without_whatsapp_configured_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("WHATSAPP_ACCESS_TOKEN", raising=False)
    get_settings.cache_clear()

    main.recover_whatsapp()  # a warning (WhatsAppError), never raises
