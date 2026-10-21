"""Office hours as the bot says them when it hands off (app.bot.prompts), on business days
(app.business_hours). The clock is always fixed. October 2026: the 5th is a Monday and Monday
12 is a national holiday; November 2026: Monday 23 is a national holiday."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.bot.identity import Identity
from app.bot.prompts import (
    build_user_turn,
    describe_office_hours,
    handoff_notice,
    next_opening,
    payment_days_text,
    urgent_handoff_notice,
)
from app.business_hours import Calendar, OpeningHours

TZ = ZoneInfo("America/Argentina/Cordoba")
OFFICE = OpeningHours(frozenset(range(5)), time(9), time(17))  # Monday to Friday, 9 to 17
CONTACT = "llamá a la guardia al 351 000-0000."


def hours(
    start: str, end: str, weekdays: list[int], calendar: Calendar | None = None
) -> OpeningHours:
    return OpeningHours(
        frozenset(weekdays),
        time.fromisoformat(start),
        time.fromisoformat(end),
        calendar or Calendar(),
    )


def at(day: int, hour: int, minute: int = 0, month: int = 10) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=TZ)


def test_describe_office_hours() -> None:
    assert describe_office_hours(OFFICE) == "de lunes a viernes de 9 a 17"
    assert describe_office_hours(hours("08:30", "13:00", [0, 2, 4])) == (
        "los lunes, miércoles y viernes de 8:30 a 13"
    )
    assert describe_office_hours(hours("09:00", "12:00", [5])) == "los sábados de 9 a 12"
    assert describe_office_hours(hours("09:00", "12:00", [5, 6])) == (
        "los sábados y domingos de 9 a 12"
    )


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (at(5, 7), "hoy a partir de las 9"),  # Monday before opening
        (at(6, 18), "mañana a partir de las 9"),  # Tuesday after closing
        (at(16, 18), "el lunes a partir de las 9"),  # Friday after closing
        (at(17, 12), "el lunes a partir de las 9"),  # Saturday
        (at(18, 23), "mañana a partir de las 9"),  # Sunday night
        (at(9, 18), "el martes a partir de las 9"),  # Friday before the holiday Monday 12
        (at(11, 23), "el martes a partir de las 9"),  # Sunday night, Monday holiday
    ],
)
def test_next_opening(now: datetime, expected: str) -> None:
    assert next_opening(now, OFFICE) == expected


def test_handoff_notice() -> None:
    inside = handoff_notice(at(7, 10), OFFICE)
    outside = handoff_notice(at(16, 17), OFFICE)  # 17:00 is closed

    assert "a la brevedad" in inside and "fuera" not in inside
    assert "fuera del horario de atención (de lunes a viernes de 9 a 17)" in outside
    assert outside.endswith("el lunes a partir de las 9.")


def test_urgent_handoff_notice() -> None:
    inside = urgent_handoff_notice(at(7, 10), OFFICE, CONTACT)
    outside = urgent_handoff_notice(at(17, 22), OFFICE, CONTACT)
    no_contact = urgent_handoff_notice(at(17, 22), OFFICE, "  ")

    assert inside == handoff_notice(at(7, 10), OFFICE)
    assert "el lunes a partir de las 9." in outside and outside.endswith(CONTACT)
    assert "el lunes a partir de las 9." in no_contact
    assert no_contact.endswith("avisale al encargado del edificio.")


# --- Holidays and closed days ----------------------------------------------------------------


def test_on_a_national_holiday_the_bot_does_not_promise_today() -> None:
    notice = handoff_notice(at(12, 10), OFFICE)  # Monday 12, 10:00: inside the hours
    assert "a la brevedad" not in notice
    assert "Hoy es feriado y el estudio no atiende" in notice
    assert notice.endswith("mañana a partir de las 9.")  # Tuesday 13


def test_on_a_closed_day_loaded_by_hand() -> None:
    closed = hours(
        "09:00",
        "17:00",
        list(range(5)),
        Calendar(closed={date(2026, 12, 24): "cierre del estudio"}),
    )
    notice = handoff_notice(at(24, 11, month=12), closed)  # Thursday 24/12
    assert "Hoy el estudio está cerrado" in notice
    assert "cierre del estudio" not in notice  # the reason is only for the studio
    assert notice.endswith("el lunes a partir de las 9.")  # Friday 25 Navidad, then Monday 28
    # The day before, after closing: no promise of "mañana".
    assert handoff_notice(at(23, 18, month=12), closed).endswith("el lunes a partir de las 9.")


def test_a_holiday_marked_open_anyway_is_office_hours() -> None:
    open_anyway = hours(
        "09:00", "17:00", list(range(5)), Calendar(open_anyway={date(2026, 10, 12)})
    )
    assert "a la brevedad" in handoff_notice(at(12, 10), open_anyway)
    assert next_opening(at(9, 18), open_anyway) == "el lunes a partir de las 9"


def test_the_friday_before_a_long_weekend_promises_tuesday() -> None:
    notice = handoff_notice(at(20, 18, month=11), OFFICE)  # Friday 20/11; Monday 23 holiday
    assert notice.endswith("el martes a partir de las 9.")


def test_an_urgency_on_a_holiday_adds_who_to_call() -> None:
    notice = urgent_handoff_notice(at(12, 10), OFFICE, CONTACT)
    assert "Hoy es feriado" in notice and notice.endswith(CONTACT)


# --- What the model gets ---------------------------------------------------------------------


def test_payment_days_skip_weekends_and_holidays() -> None:
    # Thursday 15/10: business days 15, 14, 13 and then Friday 9 (Monday 12 holiday).
    text = payment_days_text(at(15, 11), OFFICE)
    assert text.startswith("Un pago del viernes 09/10 en adelante lleva 3 días hábiles o menos")
    # Without the holiday it would have been Monday 12.
    open_anyway = hours(
        "09:00", "17:00", list(range(5)), Calendar(open_anyway={date(2026, 10, 12)})
    )
    assert payment_days_text(at(15, 11), open_anyway).startswith("Un pago del lunes 12/10")


def test_the_user_turn_says_when_today_is_a_holiday() -> None:
    who = Identity()
    turn = build_user_turn("hola", who=who, now=at(12, 10), hours=OFFICE, first_message=True)
    assert (
        "Horario de atención: de lunes a viernes de 9 a 17, días hábiles. Hoy es feriado." in turn
    )
    assert "NO estamos en horario de atención" in turn
    assert "Días hábiles para «ya pagué»: Un pago del martes 06/10" in turn
    regular = build_user_turn("hola", who=who, now=at(7, 10), hours=OFFICE, first_message=True)
    assert "Hoy es feriado" not in regular and "Ahora estamos en horario" in regular
