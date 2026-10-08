"""How the panel shows dates, phones, buildings, money and durations: in Argentina's time, short
and never technical (no seconds, microseconds, time zone nor E.164). Used from Python and, as
Jinja filters, from the templates (registered by setup_admin)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import phonenumbers

from app.bot.unit_search import display_building_name
from app.config import Settings

DEFAULT_TIMEZONE = Settings.model_fields["timezone"].default
ARGENTINA = 54


def _local(value: datetime, timezone: str) -> datetime:
    if value.tzinfo is None:  # naive values are stored in UTC
        value = value.replace(tzinfo=UTC)
    return value.astimezone(ZoneInfo(timezone))


def when(
    value: datetime | None, now: datetime | None = None, timezone: str = DEFAULT_TIMEZONE
) -> str:
    """For lists: "hoy 11:32", "ayer 18:05", "07/10 11:32" (this year) or "07/10/2026"."""
    if value is None:
        return "—"
    local = _local(value, timezone)
    today = _local(now or datetime.now(UTC), timezone).date()
    if local.date() == today:
        return f"hoy {local:%H:%M}"
    if local.date() == today - timedelta(days=1):
        return f"ayer {local:%H:%M}"
    if local.year == today.year:
        return f"{local:%d/%m %H:%M}"
    return f"{local:%d/%m/%Y}"


def ago(value: datetime | None, now: datetime) -> str:
    """How long ago: "recién", "hace 5 min", "hace 3 h", "hace 2 días"."""
    if value is None:
        return ""
    seconds = max(0, (now - value).total_seconds())
    if seconds < 60:
        return "recién"
    if seconds < 3600:
        return f"hace {int(seconds // 60)} min"
    if seconds < 86400:
        return f"hace {int(seconds // 3600)} h"
    days = int(seconds // 86400)
    return "hace 1 día" if days == 1 else f"hace {days} días"


def full(value: datetime | None, timezone: str = DEFAULT_TIMEZONE) -> str:
    """For a record's page: "07/10/2026 11:32"."""
    if value is None:
        return "—"
    return f"{_local(value, timezone):%d/%m/%Y %H:%M}"


def day(value: date | datetime | None, timezone: str = DEFAULT_TIMEZONE) -> str:
    """A date: "07/10/2026"."""
    if value is None:
        return "—"
    if isinstance(value, datetime):
        value = _local(value, timezone).date()
    return f"{value:%d/%m/%Y}"


def phone(value: str | None) -> str:
    """ "351 555-0101" for an Argentine number (as people say it), "+34 600 00 00 00" for
    another country, the text as is when it is not a number."""
    if not value:
        return "—"
    try:
        number = phonenumbers.parse(value, None)
    except phonenumbers.NumberParseException:
        return value
    if not phonenumbers.is_possible_number(number):
        return value
    text = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
    if number.country_code == ARGENTINA:
        for prefix in ("+54 9 ", "+54 "):
            if text.startswith(prefix):
                return text[len(prefix) :]
    return text


def building(name: str | None) -> str:
    """A building as the whole panel shows it: its name without the ConsorPlus code."""
    return display_building_name(name) if name else "—"


def money(value: Decimal | float | int | None, currency: str = "$") -> str:
    """ "$ 45.230,50" / "US$ 0,20": Argentine separators."""
    if value is None:
        return "—"
    text = f"{Decimal(value):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{currency} {text}"


def duration(seconds: float | int | None) -> str:
    """ "45 s", "12 min", "1 h 05 min"."""
    if seconds is None:
        return "—"
    seconds = round(float(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"


def number(value: int | float) -> str:
    """ "2.450"."""
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.1f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{int(value):,}".replace(",", ".")
