"""Bot texts and office hours in effect: the admin panel's value (bot_settings) when not
empty, otherwise the .env setting of the same name (or its default).

The panel row is cached for CACHE_SECONDS, so a change reaches the bot within a minute
without restarting (right away in the process that saved it: see invalidate_bot_config).
Only the row is cached; the merge with Settings runs every time, so callers with their own
Settings (tests, evals) still get their values when the panel is empty.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import time as dtime
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import BotSettings

CACHE_SECONDS = 60
BOT_SETTINGS_ID = 1
# Texts of bot_settings that override a .env setting of the same name.
ENV_TEXTS = ("emergency_contact_text", "autogestion_url", "payment_code_how_to")
# Office hours, also backed by .env; an invalid panel value is ignored.
HOURS = ("office_hours_start", "office_hours_end")
# Texts with no .env setting (empty: the bot's built-in behavior).
PANEL_ONLY = ("welcome_message", "out_of_hours_text")
FIELDS = (*ENV_TEXTS, *HOURS, "office_weekdays", *PANEL_ONLY)


@dataclass(frozen=True)
class BotConfig:
    welcome_message: str
    office_hours_start: str
    office_hours_end: str
    office_weekdays: list[int]
    out_of_hours_text: str
    autogestion_url: str
    emergency_contact_text: str
    payment_code_how_to: str

    @property
    def hours(self) -> tuple[str, str, list[int]]:
        return self.office_hours_start, self.office_hours_end, self.office_weekdays


def parse_hour(value: str) -> str | None:
    """ "9:00" or "09:00" -> "09:00"; None when it is not a valid HH:MM."""
    try:
        hour, minute = value.strip().split(":")
        return dtime(int(hour), int(minute)).strftime("%H:%M")
    except ValueError:
        return None


def parse_weekdays(value: str) -> list[int] | None:
    """ "0,1,2, 3,4" -> [0, 1, 2, 3, 4] (0 = Monday); None when invalid or empty."""
    try:
        days = sorted({int(item) for item in value.replace(" ", "").split(",") if item})
    except ValueError:
        return None
    if not days or any(d < 0 or d > 6 for d in days):
        return None
    return days


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
                row = session.get(BotSettings, BOT_SETTINGS_ID)
                self._values = {f: getattr(row, f) for f in FIELDS} if row else {}
                self._loaded_at = now
            return self._values

    def clear(self) -> None:
        with self._lock:
            self._values = None


_cache = _RowCache()


def invalidate_bot_config() -> None:
    """Forget the cached panel row (after saving it, and in tests)."""
    _cache.clear()


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def load_bot_config(session: Session, settings: Settings) -> BotConfig:
    panel = _cache.get(session)
    values: dict[str, Any] = {name: _text(panel.get(name)) for name in PANEL_ONLY}
    for name in ENV_TEXTS:
        values[name] = _text(panel.get(name)) or getattr(settings, name)
    for name in HOURS:
        values[name] = parse_hour(_text(panel.get(name))) or getattr(settings, name)
    weekdays = parse_weekdays(_text(panel.get("office_weekdays")))
    values["office_weekdays"] = weekdays or list(settings.office_weekdays)
    return BotConfig(**values)
