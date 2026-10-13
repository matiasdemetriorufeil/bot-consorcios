"""How the panel shows dates, phones, buildings, money and durations. Invented values only."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.admin import formatting

CBA = ZoneInfo("America/Argentina/Cordoba")
NOW = datetime(2026, 10, 7, 15, 0, tzinfo=CBA)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (datetime(2026, 10, 7, 11, 32, 45, 893754, tzinfo=CBA), "hoy 11:32"),
        (datetime(2026, 10, 6, 18, 5, tzinfo=CBA), "ayer 18:05"),
        (datetime(2026, 9, 30, 8, 0, tzinfo=CBA), "30/09 08:00"),
        (datetime(2025, 12, 31, 23, 59, tzinfo=CBA), "31/12/2025"),
        # Stored in UTC: 02:30 UTC of the 8th is 23:30 of the 7th in Córdoba ("hoy").
        (datetime(2026, 10, 8, 2, 30, tzinfo=UTC), "hoy 23:30"),
        # A naive value is UTC.
        (datetime(2026, 10, 7, 14, 32), "hoy 11:32"),
        (None, "—"),
    ],
)
def test_when(value: datetime | None, expected: str) -> None:
    assert formatting.when(value, NOW) == expected


def test_when_never_shows_seconds_nor_zone() -> None:
    text = formatting.when(datetime(2026, 10, 7, 11, 32, 45, tzinfo=UTC), NOW)
    assert text.count(":") == 1 and "+" not in text and "UTC" not in text and "-03" not in text


def test_full_and_day() -> None:
    value = datetime(2026, 10, 7, 14, 32, 45, tzinfo=UTC)
    assert formatting.full(value) == "07/10/2026 11:32"
    assert formatting.day(value) == "07/10/2026"
    assert formatting.day(date(2026, 1, 5)) == "05/01/2026"
    assert formatting.full(None) == formatting.day(None) == "—"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("+5493515550101", "351 555-0101"),  # Córdoba, mobile
        ("+543515550101", "351 555-0101"),  # Córdoba, landline
        ("+5491155550101", "11 5555-0101"),  # Buenos Aires
        ("+5492994555010", "299 455-5010"),  # another area code
        ("+34600000000", "+34 600 00 00 00"),  # another country
        ("+12025550101", "+1 202-555-0101"),
        ("no es un número", "no es un número"),
        ("+54", "+54"),
        ("", "—"),
        (None, "—"),
    ],
)
def test_phone(value: str | None, expected: str) -> None:
    assert formatting.phone(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("901 TORRE EJEMPLO", "TORRE EJEMPLO"),
        ("007 LOS PINOS", "LOS PINOS"),
        ("TORRE SIN CODIGO", "TORRE SIN CODIGO"),
        (None, "—"),
    ],
)
def test_building(value: str | None, expected: str) -> None:
    assert formatting.building(value) == expected


def test_money_number_and_duration() -> None:
    assert formatting.money(Decimal("45230.5")) == "$ 45.230,50"
    assert formatting.money(Decimal("0.2"), "US$") == "US$ 0,20"
    assert formatting.money(None) == "—"
    assert formatting.number(2450) == "2.450"
    assert formatting.number(3.5) == "3,5"
    assert formatting.duration(45) == "45 s"
    assert formatting.duration(720.4) == "12 min"
    assert formatting.duration(3900) == "1 h 05 min"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (30, "recién"),
        (60, "hace 1 min"),
        (25 * 60, "hace 25 min"),
        (3600, "hace 1 hora"),
        (3 * 3600 + 59, "hace 3 horas"),
        (86400, "hace 1 día"),
        (4 * 86400, "hace 4 días"),
    ],
)
def test_ago_never_says_1_dias(seconds: int, expected: str) -> None:
    now = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
    assert formatting.ago(now - timedelta(seconds=seconds), now) == expected


def test_plural() -> None:
    assert formatting.plural(1, "día", "días") == "1 día"
    assert formatting.plural(2, "día", "días") == "2 días"
    assert formatting.plural(0, "hora", "horas") == "0 horas"
