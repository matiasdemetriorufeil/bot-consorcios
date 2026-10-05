"""Bot settings: the admin panel's value wins over .env; empty falls back. Invented texts."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from app.bot import bot_config
from app.bot.bot_config import CACHE_SECONDS, invalidate_bot_config, load_bot_config
from app.bot.prompts import handoff_notice, urgent_handoff_notice
from app.db.models import BotSettings
from tests.admin.conftest import FakeClock, admin_settings

ENV = admin_settings(
    office_hours_start="08:00",
    office_hours_end="16:00",
    office_weekdays=[0, 1, 2, 3, 4, 5],
    emergency_contact_text="contacto de .env",
    autogestion_url="https://env.example.com",
    payment_code_how_to="cómo pagar según .env",
)
SATURDAY_NIGHT = datetime(2026, 10, 3, 22, 0, tzinfo=ZoneInfo("America/Argentina/Cordoba"))
WEEKDAYS = [0, 1, 2, 3, 4]


def _row(session: Session, **values: str | None) -> BotSettings:
    row = session.get(BotSettings, 1)
    assert row is not None  # created by the migration
    for name, value in values.items():
        setattr(row, name, value)
    session.flush()
    invalidate_bot_config()
    return row


def test_empty_panel_uses_env(db_session: Session) -> None:
    cfg = load_bot_config(db_session, ENV)
    assert cfg.hours == ("08:00", "16:00", [0, 1, 2, 3, 4, 5])
    assert cfg.emergency_contact_text == "contacto de .env"
    assert cfg.autogestion_url == "https://env.example.com"
    assert cfg.payment_code_how_to == "cómo pagar según .env"
    assert cfg.welcome_message == "" and cfg.out_of_hours_text == ""


def test_empty_panel_and_env_use_the_defaults(db_session: Session) -> None:
    cfg = load_bot_config(db_session, admin_settings())
    assert cfg.hours == ("09:00", "17:00", [0, 1, 2, 3, 4])
    assert "Pago Mis Cuentas" in cfg.payment_code_how_to
    assert cfg.autogestion_url == ""


def test_panel_values_win_over_env(db_session: Session) -> None:
    _row(
        db_session,
        welcome_message="¡Hola! Te escribe el estudio.",
        office_hours_start="10:00",
        office_hours_end="18:30",
        office_weekdays="1,3",
        out_of_hours_text="Te respondemos el próximo día hábil.",
        emergency_contact_text="contacto del panel",
        autogestion_url="https://panel.example.com",
        payment_code_how_to="cómo pagar según el panel",
    )
    cfg = load_bot_config(db_session, ENV)
    assert cfg.hours == ("10:00", "18:30", [1, 3])
    assert cfg.welcome_message == "¡Hola! Te escribe el estudio."
    assert cfg.out_of_hours_text == "Te respondemos el próximo día hábil."
    assert cfg.emergency_contact_text == "contacto del panel"
    assert cfg.autogestion_url == "https://panel.example.com"
    assert cfg.payment_code_how_to == "cómo pagar según el panel"


def test_blank_or_invalid_panel_values_fall_back_to_env(db_session: Session) -> None:
    _row(
        db_session,
        emergency_contact_text="   ",
        payment_code_how_to="",
        office_hours_start="25:00",
        office_weekdays="lunes",
    )
    cfg = load_bot_config(db_session, ENV)
    assert cfg.emergency_contact_text == "contacto de .env"
    assert cfg.payment_code_how_to == "cómo pagar según .env"
    assert cfg.office_hours_start == "08:00"
    assert cfg.office_weekdays == [0, 1, 2, 3, 4, 5]


def test_panel_row_is_cached_for_a_minute(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock()
    monkeypatch.setattr(bot_config._cache, "clock", clock)
    row = _row(db_session, payment_code_how_to="primero")
    assert load_bot_config(db_session, ENV).payment_code_how_to == "primero"

    row.payment_code_how_to = "después"
    db_session.flush()
    clock.advance(CACHE_SECONDS - 1)
    assert load_bot_config(db_session, ENV).payment_code_how_to == "primero"
    clock.advance(2)
    assert load_bot_config(db_session, ENV).payment_code_how_to == "después"

    row.payment_code_how_to = "al guardar en el panel"
    db_session.flush()
    invalidate_bot_config()
    assert load_bot_config(db_session, ENV).payment_code_how_to == "al guardar en el panel"


def test_out_of_hours_text_replaces_the_automatic_notice() -> None:
    text = "Te respondemos el próximo día hábil."
    notice = handoff_notice(SATURDAY_NIGHT, "09:00", "17:00", WEEKDAYS, text)
    assert notice == f"Ya le pasé tu consulta a una persona del estudio. {text}"
    urgent = urgent_handoff_notice(
        SATURDAY_NIGHT, "09:00", "17:00", WEEKDAYS, "llamá al 100.", text
    )
    assert urgent == f"{notice} Para la urgencia, mientras tanto: llamá al 100."

    monday_morning = datetime(2026, 10, 5, 10, 0, tzinfo=SATURDAY_NIGHT.tzinfo)
    inside = handoff_notice(monday_morning, "09:00", "17:00", WEEKDAYS, text)
    assert text not in inside and "a la brevedad" in inside
