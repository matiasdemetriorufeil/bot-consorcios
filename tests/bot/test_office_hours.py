"""Office hours as the bot says them when it hands off (app.bot.prompts)."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.bot.prompts import (
    describe_office_hours,
    handoff_notice,
    next_opening,
    urgent_handoff_notice,
)

TZ = ZoneInfo("America/Argentina/Cordoba")
WEEKDAYS = [0, 1, 2, 3, 4]


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """October 2026: the 5th is a Monday."""
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


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
