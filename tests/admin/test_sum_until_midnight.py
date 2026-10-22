"""The SUM's slots that ended at 23:59 now end at 00:00 (migration sum_slots_until_midnight).
A slot from 20:00 to 00:00 ends at midnight: the availability, the panel's agenda and the bot
show it right, and a reservation already made in it is still there, with the same ids.
Invented data only; the clock is fixed."""

import importlib.util
from datetime import date, datetime, time
from pathlib import Path
from types import ModuleType
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.admin.amenities import AmenitiesView
from app.amenities.booking import availability, describe_slot, slot_end
from app.bot.tools import ToolContext, run_tool
from app.db.models import Amenity, AmenitySlot, Reservation, ReservationSource
from tests.admin.conftest import Panel
from tests.admin.test_amenities import _AgendaParser
from tests.bot import factories as f

CBA = ZoneInfo("America/Argentina/Cordoba")
TZ = "America/Argentina/Cordoba"
NOW = datetime(2026, 10, 5, 10, 0, tzinfo=CBA)  # Monday
FRIDAY = date(2026, 10, 9)
OWNER = "+5493515550601"
VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _migration() -> ModuleType:
    path = next(VERSIONS.glob("*_sum_slots_until_midnight.py"))
    spec = importlib.util.spec_from_file_location("sum_slots_until_midnight", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AmenitiesView, "now", lambda self: NOW)


@pytest.fixture
def world(db_session: Session) -> dict[str, int]:
    """A SUM with the night slot as it was loaded (20:00 to 23:59, Fridays) and a reservation
    already made in it; then the migration's step."""
    building = f.building(db_session, "095 TORRE MEDIANOCHE")
    unit = f.unit(db_session, building, "03-D")
    f.link(db_session, unit, f.person(db_session, "Dueña Inventada", phone=OWNER))
    amenity = Amenity(building_id=building.id, max_advance_days=30, bot_booking_enabled=True)
    night = AmenitySlot(weekday=4, start_time=time(20), end_time=time(23, 59))
    noon = AmenitySlot(weekday=4, start_time=time(12), end_time=time(16))
    whole_day = AmenitySlot(weekday=5, start_time=time(0), end_time=time(23, 59))
    amenity.slots = [night, noon, whole_day]
    db_session.add(amenity)
    db_session.flush()
    reservation = Reservation(
        amenity_id=amenity.id, slot_id=night.id, date=FRIDAY, unit_id=unit.id,
        source=ReservationSource.PANEL,
    )  # fmt: skip
    db_session.add(reservation)
    db_session.commit()
    db_session.execute(text(_migration().UNTIL_MIDNIGHT))
    db_session.commit()
    db_session.expire_all()
    return {
        "amenity": amenity.id, "night": night.id, "noon": noon.id, "whole_day": whole_day.id,
        "reservation": reservation.id, "unit": unit.id,
    }  # fmt: skip


def test_only_2359_changes_in_place(db_session: Session, world: dict[str, int]) -> None:
    slots = {s.id: (s.start_time, s.end_time) for s in db_session.scalars(select(AmenitySlot))}
    assert slots[world["night"]] == (time(20), time(0))  # same id, now until midnight
    assert slots[world["noon"]] == (time(12), time(16))
    # 00:00 to 23:59 is left alone: 00:00 to 00:00 would be empty.
    assert slots[world["whole_day"]] == (time(0), time(23, 59))
    reservation = db_session.get(Reservation, world["reservation"])
    assert reservation is not None and reservation.slot_id == world["night"]
    # Again: nothing left to change.
    assert db_session.execute(text(_migration().UNTIL_MIDNIGHT)).rowcount == 0


def test_it_ends_at_midnight_and_says_so(db_session: Session, world: dict[str, int]) -> None:
    night = db_session.get(AmenitySlot, world["night"])
    assert night is not None
    assert describe_slot(night) == "20:00 a 00:00"  # not «del día siguiente»
    assert slot_end(FRIDAY, night, TZ) == datetime(2026, 10, 10, 0, 0, tzinfo=CBA)
    overnight = AmenitySlot(weekday=4, start_time=time(20), end_time=time(2))
    assert describe_slot(overnight) == "20:00 a 02:00 (del día siguiente)"


def test_availability_keeps_the_reservation(db_session: Session, world: dict[str, int]) -> None:
    amenity = db_session.get(Amenity, world["amenity"])
    assert amenity is not None
    days = availability(db_session, amenity, FRIDAY, FRIDAY, now=NOW, timezone=TZ)
    night = next(d for d in days if d.slot.id == world["night"])
    assert night.reservation is not None and night.reservation.id == world["reservation"]
    noon = next(d for d in days if d.slot.id == world["noon"])
    assert noon.reservation is None and not noon.problems  # still bookable


def test_the_agenda_draws_it_until_midnight(logged_in: Panel, world: dict[str, int]) -> None:
    response = logged_in.client.get(f"/admin/amenities/{world['amenity']}/week?start=2026-10-05")
    assert response.status_code == 200
    parser = _AgendaParser()
    parser.feed(response.text)
    # The grid goes from 00:00 (the Saturday slot) to 24: nothing of the next day is added.
    assert parser.hours[-1] == "23:00"
    night = next(b for b in parser.blocks if b["slot"] == world["night"])
    assert night["day"] == "2026-10-09" and night["state"] == "reserved"
    assert night["text"].startswith("20:00 a 00:00")
    assert "del día siguiente" not in night["title"]
    # 20 to 24 over 0 to 24.
    assert night["top"] == pytest.approx(100 * 20 / 24, abs=1e-3)
    assert night["height"] == pytest.approx(100 * 4 / 24, abs=1e-3)


def _ctx(db_session: Session) -> ToolContext:
    return ToolContext(
        session=db_session,
        phone=OWNER,
        refresh_debt=lambda unit_id: None,  # type: ignore[arg-type,return-value]
        now=NOW,
    )


def test_the_bot_says_20_to_00(db_session: Session, world: dict[str, int]) -> None:
    # The owner's reservation, still there and «20:00 a 00:00».
    mine = run_tool(_ctx(db_session), "my_sum_reservations", {})
    [item] = mine["reservations"]
    assert item["reservation_id"] == world["reservation"] and item["time"] == "20:00 a 00:00"
    # The next Friday it is free, and offered with the same text.
    free = run_tool(_ctx(db_session), "sum_availability", {"date_from": "2026-10-16"})
    night = next(i for i in free["free"] if i["slot"] == "20:00")
    assert night["time"] == "20:00 a 00:00"
