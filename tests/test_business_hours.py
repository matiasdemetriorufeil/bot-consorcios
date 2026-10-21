"""Business days and opening hours (app.business_hours): pure functions, no database, the
clock always given. October 2026: Monday 12 is a national holiday (Día del Respeto a la
Diversidad Cultural); November 2026: Monday 23 too (Día de la Soberanía Nacional)."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.business_hours import (
    Calendar,
    OpeningHours,
    national_holiday,
    national_holidays,
    when_text,
)

TZ = ZoneInfo("America/Argentina/Cordoba")
# Monday to Saturday, 8:00 to 20:00 (the providers' defaults), in a week with no holidays.
HOURS = OpeningHours(frozenset(range(6)), time(8), time(20))
FRIDAY = date(2026, 10, 16)
SATURDAY = date(2026, 10, 17)
MONDAY = date(2026, 10, 19)
HOLIDAY = date(2026, 10, 12)  # Monday
LONG_WEEKEND_FRIDAY = date(2026, 11, 20)  # Monday 23 is a holiday: back on Tuesday 24
OFFICE = OpeningHours(frozenset(range(5)), time(9), time(17))  # Monday to Friday


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), TZ)


@pytest.mark.parametrize(
    ("moment", "is_open"),
    [
        (at(FRIDAY, 7, 59), False),
        (at(FRIDAY, 8), True),
        (at(FRIDAY, 19, 59), True),
        (at(FRIDAY, 20), False),
        (at(date(2026, 10, 18), 12), False),  # Sunday
    ],
)
def test_is_open(moment: datetime, is_open: bool) -> None:
    assert HOURS.is_open(moment) is is_open


def test_next_opening() -> None:
    assert HOURS.next_opening(at(FRIDAY, 21)) == at(SATURDAY, 8)
    assert HOURS.next_opening(at(SATURDAY, 20, 30)) == at(MONDAY, 8)
    assert HOURS.next_opening(at(FRIDAY, 6)) == at(FRIDAY, 8)
    assert HOURS.next_opening(at(FRIDAY, 10)) == at(FRIDAY, 10)  # already open


@pytest.mark.parametrize(
    ("start", "hours", "end"),
    [
        (at(FRIDAY, 19), 4, at(SATURDAY, 11)),
        (at(SATURDAY, 19, 30), 4, at(MONDAY, 11, 30)),
        (at(FRIDAY, 10), 4, at(FRIDAY, 14)),
        (at(FRIDAY, 21), 4, at(SATURDAY, 12)),  # starts counting at the opening
        (at(FRIDAY, 19), 13, at(SATURDAY, 20)),  # 1 h Friday + 12 h Saturday: its close
        (at(FRIDAY, 19), 14, at(MONDAY, 9)),  # and one more hour: Monday
    ],
)
def test_add_open_time(start: datetime, hours: int, end: datetime) -> None:
    assert HOURS.add_open_time(start, timedelta(hours=hours)) == end


def test_utc_moments_are_read_in_cordoba() -> None:
    utc = at(FRIDAY, 21).astimezone(ZoneInfo("UTC"))  # Saturday 00:00 UTC
    assert HOURS.next_opening(utc) == at(SATURDAY, 8)


def test_when_text() -> None:
    now = at(FRIDAY, 21)
    assert when_text(at(FRIDAY, 22), now) == "hoy a las 22:00"
    assert when_text(at(SATURDAY, 8), now) == "mañana a las 8:00"
    assert when_text(at(MONDAY, 8), now) == "el lunes a las 8:00"
    assert when_text(at(date(2026, 10, 26), 8, 30), now) == "el 26/10 a las 8:30"


# --- Holidays and closed days ----------------------------------------------------------------


def test_national_holidays_come_from_the_library_for_any_year() -> None:
    assert national_holiday(HOLIDAY) is not None
    assert national_holiday(date(2027, 7, 9)) is not None  # Independencia, another year
    assert national_holiday(FRIDAY) is None
    days = [day for day, _ in national_holidays(2027)]
    assert days[0] == date(2027, 1, 1) and date(2027, 12, 25) in days and days == sorted(days)


def test_a_national_holiday_is_not_a_business_day() -> None:
    assert not HOURS.is_business_day(HOLIDAY)
    assert not HOURS.is_open(at(HOLIDAY, 10))
    assert HOURS.next_opening(at(date(2026, 10, 10), 21)) == at(date(2026, 10, 13), 8)
    # Saturday 19:00 + 4 h: one hour on Saturday, then Tuesday (Sunday closed, Monday holiday).
    assert HOURS.add_open_time(at(date(2026, 10, 10), 19), timedelta(hours=4)) == at(
        date(2026, 10, 13), 11
    )


def test_a_closed_day_loaded_by_hand_is_skipped() -> None:
    hours = OpeningHours(
        frozenset(range(6)), time(8), time(20), Calendar(closed={SATURDAY: "cierre del estudio"})
    )
    assert not hours.is_open(at(SATURDAY, 10))
    assert hours.next_opening(at(FRIDAY, 21)) == at(MONDAY, 8)
    assert hours.add_open_time(at(FRIDAY, 19), timedelta(hours=4)) == at(MONDAY, 11)
    assert not hours.calendar.is_holiday(SATURDAY)  # closed, but not a holiday


def test_a_holiday_marked_open_anyway_is_a_business_day() -> None:
    hours = OpeningHours(frozenset(range(5)), time(9), time(17), Calendar(open_anyway={HOLIDAY}))
    assert hours.is_business_day(HOLIDAY)
    assert hours.is_open(at(HOLIDAY, 10))
    assert hours.next_opening(at(date(2026, 10, 9), 18)) == at(HOLIDAY, 9)


def test_the_friday_before_a_long_weekend_opens_next_on_tuesday() -> None:
    opening = OFFICE.next_opening(at(LONG_WEEKEND_FRIDAY, 18))
    assert opening == at(date(2026, 11, 24), 9)
    assert when_text(opening, at(LONG_WEEKEND_FRIDAY, 18)) == "el martes a las 9:00"


def test_business_days_back_skip_weekends_holidays_and_closed_days() -> None:
    hours = OpeningHours(
        frozenset(range(5)), time(9), time(17), Calendar(closed={date(2026, 10, 14): "cierre"})
    )
    # Thursday 15: Wednesday 14 closed, Monday 12 holiday, Saturday and Sunday out.
    assert hours.business_days_back(date(2026, 10, 15), 4) == [
        date(2026, 10, 15),
        date(2026, 10, 13),
        date(2026, 10, 9),
        date(2026, 10, 8),
    ]
