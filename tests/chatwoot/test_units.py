"""Pure pieces: history, office hours, labels, settings and the /dev/chat page."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.bot.identity import Identity, UnitAccess
from app.bot.prompts import (
    describe_office_hours,
    handoff_notice,
    next_opening,
    urgent_handoff_notice,
)
from app.bot.tools import Handoff
from app.chatwoot.handoff import contact_attributes, handoff_labels, slug
from app.chatwoot.history import build_history
from app.config import Settings, get_settings
from app.db.models import PersonRole
from app.llm import AssistantMessage, UserMessage
from app.main import app
from tests.chatwoot.fakes import history_message

TZ = ZoneInfo("America/Argentina/Cordoba")
WEEKDAYS = [0, 1, 2, 3, 4]


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """October 2026: the 5th is a Monday."""
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


# --- History --------------------------------------------------------------------------------


def test_build_history_alternates_and_skips_notes() -> None:
    messages = [
        history_message(5, "¡Hola! ¿En qué te ayudo?", 3),  # inbox greeting: dropped
        history_message(6, "hola", 0),
        history_message(7, "quiero saber mi deuda", 0),
        history_message(8, "nota privada", 1, private=True),
        history_message(9, "Conversación asignada", 2),
        history_message(10, "¿De qué edificio?", 1),
        history_message(11, "mensaje actual", 0),
        history_message(12, "posterior", 0),
    ]

    history = build_history(messages, before_id=11)

    assert history == [
        UserMessage("hola\nquiero saber mi deuda"),
        AssistantMessage("¿De qué edificio?"),
    ]


def test_build_history_limit_and_attachments() -> None:
    messages = [history_message(i, f"m{i}", i % 2) for i in range(1, 41)]
    messages.append({"id": 41, "content": None, "message_type": 0, "attachments": [
        {"file_type": "image"}]})  # fmt: skip

    history = build_history(messages, before_id=100, limit=4)

    assert history[0] == UserMessage("m38")  # always starts with the person
    assert history[-1] == UserMessage("m40\n[adjunto: image]")  # same side: joined


# --- Office hours -------------------------------------------------------------------------


def test_describe_office_hours() -> None:
    assert describe_office_hours("09:00", "17:00", WEEKDAYS) == "de lunes a viernes de 9 a 17"
    assert describe_office_hours("08:30", "13:00", [0, 2, 4]) == (
        "los lunes, miércoles y viernes de 8:30 a 13"
    )
    assert describe_office_hours("09:00", "12:00", [5]) == "los sábados de 9 a 12"
    assert describe_office_hours("09:00", "12:00", [5, 6]) == "los sábados y domingos de 9 a 12"


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (at(5, 7), "hoy a partir de las 9"),  # Monday before opening
        (at(6, 18), "mañana a partir de las 9"),  # Tuesday after closing
        (at(9, 18), "el lunes a partir de las 9"),  # Friday after closing
        (at(10, 12), "el lunes a partir de las 9"),  # Saturday
        (at(11, 23), "mañana a partir de las 9"),  # Sunday night
    ],
)
def test_next_opening(now: datetime, expected: str) -> None:
    assert next_opening(now, "09:00", WEEKDAYS) == expected


def test_handoff_notice() -> None:
    inside = handoff_notice(at(7, 10), "09:00", "17:00", WEEKDAYS)
    outside = handoff_notice(at(9, 17), "09:00", "17:00", WEEKDAYS)  # 17:00 is closed

    assert "a la brevedad" in inside and "fuera" not in inside
    assert "fuera del horario de atención (de lunes a viernes de 9 a 17)" in outside
    assert outside.endswith("el lunes a partir de las 9.")


def test_urgent_handoff_notice() -> None:
    contact = "llamá a la guardia al 351 000-0000."
    inside = urgent_handoff_notice(at(7, 10), "09:00", "17:00", WEEKDAYS, contact)
    outside = urgent_handoff_notice(at(10, 22), "09:00", "17:00", WEEKDAYS, contact)
    no_contact = urgent_handoff_notice(at(10, 22), "09:00", "17:00", WEEKDAYS, "  ")

    assert inside == handoff_notice(at(7, 10), "09:00", "17:00", WEEKDAYS)
    assert "el lunes a partir de las 9." in outside and outside.endswith(contact)
    assert "el lunes a partir de las 9." in no_contact
    assert no_contact.endswith("avisale al encargado del edificio.")


# --- Labels and contact -------------------------------------------------------------------

ANA = Identity(
    person_id=1,
    full_name="Ana Prueba",
    units=(
        UnitAccess(1, "031 RODAS II", "04-C", PersonRole.OWNER),
        UnitAccess(2, "031 RODAS II", "Cochera 3", PersonRole.OWNER),
        UnitAccess(3, "045 PEÑA ALTA", "01-A", PersonRole.TENANT),
    ),
)


def test_slug() -> None:
    assert slug("Peña Alta") == "pena-alta"
    assert slug("Rodas II") == "rodas-ii"


def test_handoff_labels() -> None:
    urgent = Handoff("emergency", "x", "urgent")
    assert handoff_labels(urgent, ANA) == [
        "emergencia",
        "edificio-rodas-ii",
        "edificio-pena-alta",
        "urgente",
    ]
    assert handoff_labels(Handoff("cualquiera", "x"), Identity()) == ["otro-motivo"]


def test_contact_attributes() -> None:
    assert contact_attributes(ANA) == {
        "unit": "RODAS II 04-C; RODAS II Cochera 3; PEÑA ALTA 01-A",
        "building": "RODAS II, PEÑA ALTA",
        "verified": True,
    }
    assert contact_attributes(Identity()) == {"verified": False}


# --- Settings -------------------------------------------------------------------------------


@pytest.mark.parametrize(("raw", "ids"), [("3", [3]), ("3, 5", [3, 5]), ("[3,5]", [3, 5])])
def test_trusted_inbox_ids_from_env(monkeypatch: pytest.MonkeyPatch, raw: str, ids: list) -> None:
    monkeypatch.setenv("CHATWOOT_TRUSTED_PHONE_INBOX_IDS", raw)

    assert Settings(_env_file=None).chatwoot_trusted_phone_inbox_ids == ids


def test_trusted_inbox_ids_default_empty() -> None:
    assert Settings(_env_file=None).chatwoot_trusted_phone_inbox_ids == []


# --- /dev/chat ------------------------------------------------------------------------------


def _get_dev_chat(settings: Settings) -> object:
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        return TestClient(app).get("/dev/chat")
    finally:
        app.dependency_overrides.clear()


def test_dev_chat_only_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    # The container's environment may define them; Settings reads it despite _env_file=None.
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("CHATWOOT_FRONTEND_URL", raising=False)
    production = _get_dev_chat(Settings(_env_file=None, chatwoot_website_token="tok"))
    development = _get_dev_chat(
        Settings(_env_file=None, app_env="development", chatwoot_website_token="tok123")
    )

    assert production.status_code == 404  # type: ignore[attr-defined]
    assert development.status_code == 200  # type: ignore[attr-defined]
    html = development.text  # type: ignore[attr-defined]
    assert '"tok123"' in html and '"http://localhost:3000"' in html and "/packs/js/sdk.js" in html


def test_dev_chat_escapes_values() -> None:
    page = _get_dev_chat(
        Settings(_env_file=None, app_env="development", chatwoot_website_token="</script><b>")
    )

    assert "</script><b>" not in page.text  # type: ignore[attr-defined]
