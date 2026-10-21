"""Business days and opening hours: the single place that decides whether a moment is inside a
schedule (the studio's office hours for the bot, the providers' hours for claims).

A business day is one of the schedule's weekdays that is not closed. Closed: a national holiday
of Argentina (the holidays library, computed per year without network) unless the studio
marked it "abre igual", or a day loaded in the panel as closed ("Días cerrados", table
closed_days). On top of that, the schedule's hours (8:00 to 20:00, say).

Everything in Córdoba time (the timezone given); datetimes may come in any timezone.
"""

import threading
import time as clock_time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from types import MappingProxyType
from typing import Any
from zoneinfo import ZoneInfo

import holidays
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ClosedDay, ClosedDayKind

TIMEZONE = "America/Argentina/Cordoba"
WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
# Far enough for any real schedule; protects against one with no open time at all.
SEARCH_DAYS = 400
CACHE_SECONDS = 60


@lru_cache(maxsize=32)
def _national(year: int) -> Mapping[date, str]:
    found = holidays.country_holidays("AR", years=year, language="es")
    return MappingProxyType(dict(found.items()))


def national_holiday(day: date) -> str | None:
    """The name of the national holiday of Argentina on that day, or None."""
    return _national(day.year).get(day)


def next_holidays(count: int, today: date | None = None) -> list[tuple[date, str]]:
    """The next `count` national holidays from today (Córdoba) on."""
    today = today or datetime.now(ZoneInfo(TIMEZONE)).date()
    found = [
        (day, name)
        for year in (today.year, today.year + 1)
        for day, name in sorted(_national(year).items())
        if day >= today
    ]
    return found[:count]


@dataclass(frozen=True)
class Calendar:
    """The days loaded in the panel: closed (date -> reason) and the national holidays the
    studio opens anyway."""

    closed: Mapping[date, str] = field(default_factory=dict)
    open_anyway: frozenset[date] = frozenset()

    def is_closed(self, day: date) -> bool:
        if day in self.closed:
            return True
        return day not in self.open_anyway and national_holiday(day) is not None

    def is_holiday(self, day: date) -> bool:
        """A national holiday the studio does not open (a closed day loaded by hand is not)."""
        return day not in self.open_anyway and national_holiday(day) is not None


@dataclass(frozen=True)
class OpeningHours:
    weekdays: frozenset[int]  # 0 = Monday
    start: time
    end: time
    calendar: Calendar = field(default_factory=Calendar)
    timezone: str = TIMEZONE

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def local(self, moment: datetime) -> datetime:
        return moment.astimezone(self.tz)

    def is_business_day(self, day: date) -> bool:
        return day.weekday() in self.weekdays and not self.calendar.is_closed(day)

    def _window(self, day: date) -> tuple[datetime, datetime]:
        return (
            datetime.combine(day, self.start, self.tz),
            datetime.combine(day, self.end, self.tz),
        )

    def is_open(self, moment: datetime) -> bool:
        local = self.local(moment)
        if not self.is_business_day(local.date()):
            return False
        opens, closes = self._window(local.date())
        return opens <= local < closes

    def next_opening(self, moment: datetime) -> datetime:
        """The moment itself if it is inside the hours, else when they open next."""
        local = self.local(moment)
        if self.is_open(local):
            return local
        day = local.date()
        for _ in range(SEARCH_DAYS):
            if self.is_business_day(day):
                opens, _closes = self._window(day)
                if opens >= local:
                    return opens
            day += timedelta(days=1)
        raise ValueError("el horario no tiene ningún momento abierto")

    def add_open_time(self, moment: datetime, amount: timedelta) -> datetime:
        """The moment when `amount` of time inside the hours has passed since `moment`
        (Friday 19:00 + 4 h, hours 8 to 20 Monday to Saturday = Saturday 11:00)."""
        current = self.local(moment)
        left = amount
        for _ in range(SEARCH_DAYS):
            current = self.next_opening(current)
            _opens, closes = self._window(current.date())
            available = closes - current
            if left <= available:
                return current + left
            left -= available
            current = closes
        raise ValueError("el horario no tiene suficiente tiempo abierto")

    def business_days_back(self, day: date, count: int) -> list[date]:
        """The last `count` business days up to `day` (itself included), newest first."""
        found: list[date] = []
        for _ in range(SEARCH_DAYS):
            if len(found) == count:
                break
            if self.is_business_day(day):
                found.append(day)
            day -= timedelta(days=1)
        return found


def day_text(day: date, today: date) -> str:
    """ "hoy", "mañana", "el martes" (within the week) or "el 24/12"."""
    days = (day - today).days
    if days <= 0:
        return "hoy"
    if days == 1:
        return "mañana"
    if days < 7:
        return f"el {WEEKDAYS[day.weekday()]}"
    return f"el {day:%d/%m}"


def hour_text(value: time) -> str:
    return f"{value.hour}:{value.minute:02d}"


def when_text(opening: datetime, now: datetime, timezone: str = TIMEZONE) -> str:
    """ "hoy a las 8:00", "mañana a las 8:00", "el lunes a las 8:00" or "el 12/10 a las 8:00"."""
    tz = ZoneInfo(timezone)
    opening = opening.astimezone(tz)
    return (
        f"{day_text(opening.date(), now.astimezone(tz).date())} a las {hour_text(opening.time())}"
    )


def weekdays_of(values: Iterable[int]) -> frozenset[int]:
    return frozenset(int(v) for v in values if 0 <= int(v) <= 6)


# --- The panel's days (closed_days), cached ---------------------------------------------------


class _CalendarCache:
    """closed_days for CACHE_SECONDS, so a change reaches the bot and the scheduler within a
    minute without restarting (right away in the process that saved it: invalidate_calendar)."""

    def __init__(self, clock: Callable[[], float] = clock_time.monotonic) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        self._value: Calendar | None = None
        self._loaded_at = 0.0

    def get(self, session: Session) -> Calendar:
        with self._lock:
            now = self.clock()
            if self._value is None or now - self._loaded_at >= CACHE_SECONDS:
                self._value = calendar_of(session.scalars(select(ClosedDay)))
                self._loaded_at = now
            return self._value

    def clear(self) -> None:
        with self._lock:
            self._value = None


def calendar_of(rows: Iterable[Any]) -> Calendar:
    closed: dict[date, str] = {}
    open_anyway: set[date] = set()
    for row in rows:
        if row.kind == ClosedDayKind.OPEN_ANYWAY:
            open_anyway.add(row.day)
        else:
            closed[row.day] = row.reason
    return Calendar(closed=MappingProxyType(closed), open_anyway=frozenset(open_anyway))


_cache = _CalendarCache()


def load_calendar(session: Session) -> Calendar:
    return _cache.get(session)


def invalidate_calendar() -> None:
    """Forget the cached days (after saving them in the panel, and in tests)."""
    _cache.clear()
