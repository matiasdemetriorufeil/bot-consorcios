"""The hours to write to providers, as pure functions (no database): whether a moment is inside
them, the next opening, adding "hours of the schedule" (only time inside them counts, holidays
excluded) and how the neighbor reads when the claim will go ("mañana a las 8:00").

Everything in Córdoba time (the timezone given); datetimes may come in any timezone.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
# Far enough for any real schedule; protects against one with no open time at all.
SEARCH_DAYS = 400


@dataclass(frozen=True)
class ProviderHours:
    weekdays: frozenset[int]  # 0 = Monday
    start: time
    end: time
    holidays: frozenset[date] = field(default_factory=frozenset)
    timezone: str = "America/Argentina/Cordoba"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def _local(self, moment: datetime) -> datetime:
        return moment.astimezone(self.tz)

    def _open_day(self, day: date) -> bool:
        return day.weekday() in self.weekdays and day not in self.holidays

    def _window(self, day: date) -> tuple[datetime, datetime]:
        return (
            datetime.combine(day, self.start, self.tz),
            datetime.combine(day, self.end, self.tz),
        )

    def is_open(self, moment: datetime) -> bool:
        local = self._local(moment)
        if not self._open_day(local.date()):
            return False
        opens, closes = self._window(local.date())
        return opens <= local < closes

    def next_opening(self, moment: datetime) -> datetime:
        """The moment itself if it is inside the hours, else when they open next."""
        local = self._local(moment)
        if self.is_open(local):
            return local
        day = local.date()
        for _ in range(SEARCH_DAYS):
            if self._open_day(day):
                opens, _closes = self._window(day)
                if opens >= local:
                    return opens
            day += timedelta(days=1)
        raise ValueError("el horario no tiene ningún momento abierto")

    def add_open_time(self, moment: datetime, amount: timedelta) -> datetime:
        """The moment when `amount` of time inside the hours has passed since `moment`
        (Friday 19:00 + 4 h, hours 8 to 20 Monday to Saturday = Saturday 11:00)."""
        current = self._local(moment)
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


def parse_dates(text: str) -> tuple[list[date], list[str]]:
    """Holidays, one per line as DD/MM/AAAA: (the dates, the lines that are not one)."""
    found: list[date] = []
    wrong: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            found.append(datetime.strptime(line, "%d/%m/%Y").date())
        except ValueError:
            wrong.append(line)
    return found, wrong


def holidays_of(text: str) -> frozenset[date]:
    return frozenset(parse_dates(text)[0])


def _hour(value: time) -> str:
    return f"{value.hour}:{value.minute:02d}"


def when_text(opening: datetime, now: datetime, timezone: str = "America/Argentina/Cordoba") -> str:
    """ "hoy a las 8:00", "mañana a las 8:00", "el lunes a las 8:00" (within the week) or
    "el 12/10 a las 8:00"."""
    tz = ZoneInfo(timezone)
    opening, today = opening.astimezone(tz), now.astimezone(tz).date()
    days = (opening.date() - today).days
    at = f"a las {_hour(opening.time())}"
    if days <= 0:
        return f"hoy {at}"
    if days == 1:
        return f"mañana {at}"
    if days < 7:
        return f"el {WEEKDAYS[opening.weekday()]} {at}"
    return f"el {opening:%d/%m} {at}"


def weekdays_of(values: Iterable[int]) -> frozenset[int]:
    return frozenset(int(v) for v in values if 0 <= int(v) <= 6)
