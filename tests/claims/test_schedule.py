"""The providers' hours (app.claims.schedule): pure functions, no database."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.claims.schedule import ProviderHours, parse_dates, when_text

TZ = ZoneInfo("America/Argentina/Cordoba")
# Monday to Saturday, 8:00 to 20:00 (the defaults).
HOURS = ProviderHours(frozenset(range(6)), time(8), time(20))
FRIDAY = date(2026, 10, 9)
SATURDAY = date(2026, 10, 10)
MONDAY = date(2026, 10, 12)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), TZ)


@pytest.mark.parametrize(
    ("moment", "is_open"),
    [
        (at(FRIDAY, 7, 59), False),
        (at(FRIDAY, 8), True),
        (at(FRIDAY, 19, 59), True),
        (at(FRIDAY, 20), False),
        (at(date(2026, 10, 11), 12), False),  # Sunday
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


def test_a_holiday_is_skipped() -> None:
    hours = ProviderHours(frozenset(range(6)), time(8), time(20), frozenset({SATURDAY}))
    assert not hours.is_open(at(SATURDAY, 10))
    assert hours.next_opening(at(FRIDAY, 21)) == at(MONDAY, 8)
    assert hours.add_open_time(at(FRIDAY, 19), timedelta(hours=4)) == at(MONDAY, 11)


def test_utc_moments_are_read_in_cordoba() -> None:
    utc = at(FRIDAY, 21).astimezone(ZoneInfo("UTC"))  # Saturday 00:00 UTC
    assert HOURS.next_opening(utc) == at(SATURDAY, 8)


def test_when_text() -> None:
    now = at(FRIDAY, 21)
    assert when_text(at(FRIDAY, 22), now) == "hoy a las 22:00"
    assert when_text(at(SATURDAY, 8), now) == "mañana a las 8:00"
    assert when_text(at(MONDAY, 8), now) == "el lunes a las 8:00"
    assert when_text(at(date(2026, 10, 19), 8, 30), now) == "el 19/10 a las 8:30"


def test_parse_dates() -> None:
    dates, wrong = parse_dates("24/12/2026\n\n 25/12/2026 \n31-12-2026\n32/01/2027")
    assert dates == [date(2026, 12, 24), date(2026, 12, 25)]
    assert wrong == ["31-12-2026", "32/01/2027"]
