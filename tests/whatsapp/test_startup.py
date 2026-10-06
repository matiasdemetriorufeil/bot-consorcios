"""On startup with CHANNEL=whatsapp, the api answers what a restart left unanswered."""

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


@pytest.mark.parametrize(("channel", "runs"), [("whatsapp", True), ("chatwoot", False)])
def test_recovery_runs_on_startup_only_for_whatsapp(
    monkeypatch: pytest.MonkeyPatch, channel: str, runs: bool
) -> None:
    monkeypatch.setenv("CHANNEL", channel)
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

    assert bot.calls == ([timedelta(minutes=20)] if runs else [])
    assert started == (["wa-recovery"] if runs else [])


def test_recovery_survives_a_missing_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(settings: object) -> None:
        raise RuntimeError("faltan WHATSAPP_ACCESS_TOKEN o WHATSAPP_PHONE_NUMBER_ID")

    monkeypatch.setattr(main.whatsapp_webhook, "build_whatsapp_bot", broken)

    main.recover_whatsapp()  # logs, never raises
