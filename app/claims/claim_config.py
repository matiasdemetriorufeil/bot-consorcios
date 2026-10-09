"""The claims' settings in effect ("Configuración de reclamos" of the admin panel): the providers'
hours, the holidays, whether urgent claims go at any time, and when providers are reminded and
the studio is told. Defaults when the row is missing or a value is invalid.

The row is cached for CACHE_SECONDS, so a change reaches the bot and the scheduler within a
minute without restarting (right away in the process that saved it: invalidate_claim_config).
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import time as dtime
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.bot.bot_config import parse_hour, parse_weekdays
from app.claims.schedule import ProviderHours, holidays_of
from app.db.models import ClaimSettings

CACHE_SECONDS = 60
SETTINGS_ID = 1
DEFAULTS: dict[str, Any] = {
    "provider_weekdays": "0,1,2,3,4,5",
    "provider_hours_start": "08:00",
    "provider_hours_end": "20:00",
    "holidays": "",
    "urgent_any_time": True,
    "reminder_hours": 4,
    "reminder_urgent_minutes": 30,
    "alert_hours": 8,
    "alert_urgent_minutes": 60,
    "stale_days": 3,
}
FIELDS = tuple(DEFAULTS)


@dataclass(frozen=True)
class ClaimConfig:
    hours: ProviderHours
    urgent_any_time: bool
    reminder: timedelta  # of open time (normal claims)
    reminder_urgent: timedelta  # real time
    alert: timedelta  # of open time
    alert_urgent: timedelta  # real time
    stale: timedelta  # real time


class _RowCache:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        self._values: dict[str, Any] | None = None
        self._loaded_at = 0.0

    def get(self, session: Session) -> dict[str, Any]:
        with self._lock:
            now = self.clock()
            if self._values is None or now - self._loaded_at >= CACHE_SECONDS:
                row = session.get(ClaimSettings, SETTINGS_ID)
                self._values = {f: getattr(row, f) for f in FIELDS} if row else {}
                self._loaded_at = now
            return self._values

    def clear(self) -> None:
        with self._lock:
            self._values = None


_cache = _RowCache()


def invalidate_claim_config() -> None:
    """Forget the cached row (after saving it, and in tests)."""
    _cache.clear()


def _positive(value: Any, default: int) -> int:
    return value if isinstance(value, int) and value > 0 else default


def build_config(values: dict[str, Any], timezone: str) -> ClaimConfig:
    def get(name: str) -> Any:
        value = values.get(name)
        return DEFAULTS[name] if value is None else value

    start = parse_hour(str(get("provider_hours_start"))) or DEFAULTS["provider_hours_start"]
    end = parse_hour(str(get("provider_hours_end"))) or DEFAULTS["provider_hours_end"]
    if end <= start:
        start, end = DEFAULTS["provider_hours_start"], DEFAULTS["provider_hours_end"]
    weekdays = parse_weekdays(str(get("provider_weekdays"))) or parse_weekdays(
        DEFAULTS["provider_weekdays"]
    )
    hours = ProviderHours(
        weekdays=frozenset(weekdays or []),
        start=dtime.fromisoformat(start),
        end=dtime.fromisoformat(end),
        holidays=holidays_of(str(get("holidays") or "")),
        timezone=timezone,
    )
    return ClaimConfig(
        hours=hours,
        urgent_any_time=bool(get("urgent_any_time")),
        reminder=timedelta(hours=_positive(get("reminder_hours"), 4)),
        reminder_urgent=timedelta(minutes=_positive(get("reminder_urgent_minutes"), 30)),
        alert=timedelta(hours=_positive(get("alert_hours"), 8)),
        alert_urgent=timedelta(minutes=_positive(get("alert_urgent_minutes"), 60)),
        stale=timedelta(days=_positive(get("stale_days"), 3)),
    )


def load_claim_config(session: Session, timezone: str) -> ClaimConfig:
    return build_config(_cache.get(session), timezone)
