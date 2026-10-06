"""Booking rules of the SUM (app.amenities.booking), against the Postgres test database.
All data is invented."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.amenities.booking import (
    BookingError,
    BookingProblem,
    availability,
    book,
    cancel,
    check_booking,
    describe_slot,
    free_slots,
    slot_end,
    slot_start,
)
from app.db.models import (
    Amenity,
    AmenitySlot,
    DebtSnapshot,
    Reservation,
    ReservationSource,
    ReservationStatus,
    SyncKind,
    Unit,
)
from tests.bot import factories as f

TZ = "America/Argentina/Cordoba"
CBA = ZoneInfo(TZ)
NOW = datetime(2026, 10, 5, 10, 0, tzinfo=CBA)  # Monday 05/10/2026 10:00
FRIDAY = date(2026, 10, 9)
SATURDAY = date(2026, 10, 10)
NEXT_FRIDAY = date(2026, 10, 16)


@dataclass
class World:
    session: Session
    amenity: Amenity
    afternoon: AmenitySlot  # Fridays 14:00 to 18:00
    night: AmenitySlot  # Fridays 20:00 to 02:00 (next day)
    unit: Unit
    other_unit: Unit
    stranger: Unit  # of another building

    def book(
        self, slot: AmenitySlot, day: date, unit: Unit | None = None, **kwargs: Any
    ) -> Reservation:
        kwargs.setdefault("now", NOW)
        kwargs.setdefault("source", ReservationSource.BOT)
        return book(
            self.session,
            self.amenity,
            slot.id,
            day,
            (unit or self.unit).id,
            timezone=TZ,
            **kwargs,
        )

    def problems(
        self, slot: AmenitySlot, day: date, unit: Unit | None = None, now: datetime = NOW
    ) -> list[BookingProblem]:
        return check_booking(
            self.session, self.amenity, slot, day, unit or self.unit, now=now, timezone=TZ
        )


@pytest.fixture
def world(db_session: Session) -> World:
    building = f.building(db_session, "088 TORRE INVENTADA")
    other_building = f.building(db_session, "089 OTRO INVENTADO")
    amenity = Amenity(
        building_id=building.id,
        min_advance_hours=24,
        max_advance_days=30,
        max_per_unit_per_month=2,
        cancel_until_hours=48,
    )
    afternoon = AmenitySlot(weekday=4, start_time=time(14), end_time=time(18), label="Tarde")
    night = AmenitySlot(weekday=4, start_time=time(20), end_time=time(2))
    amenity.slots = [afternoon, night]
    db_session.add(amenity)
    db_session.flush()
    return World(
        db_session,
        amenity,
        afternoon,
        night,
        f.unit(db_session, building, "01-A"),
        f.unit(db_session, building, "02-B"),
        f.unit(db_session, other_building, "03-C"),
    )


# --- Slots ---------------------------------------------------------------------------------


def test_a_slot_can_end_the_next_day(world: World) -> None:
    assert world.night.overnight and not world.afternoon.overnight
    assert slot_start(FRIDAY, world.night, TZ) == datetime(2026, 10, 9, 20, tzinfo=CBA)
    assert slot_end(FRIDAY, world.night, TZ) == datetime(2026, 10, 10, 2, tzinfo=CBA)
    assert slot_end(FRIDAY, world.afternoon, TZ) == datetime(2026, 10, 9, 18, tzinfo=CBA)
    assert describe_slot(world.night) == "20:00 a 02:00 (del día siguiente)"
    assert describe_slot(world.afternoon) == "14:00 a 18:00 · Tarde"


def test_overnight_reservation_belongs_to_the_day_it_starts(world: World) -> None:
    reservation = world.book(world.night, FRIDAY)

    assert reservation.date == FRIDAY
    # Saturday has no slots: the Friday night slot is not a Saturday one.
    assert world.problems(world.night, SATURDAY) == [BookingProblem.NO_SUCH_SLOT]
    # Friday 23:30, during the night slot: it already started.
    late = datetime(2026, 10, 9, 23, 30, tzinfo=CBA)
    assert world.problems(world.night, FRIDAY, world.other_unit, now=late) == [BookingProblem.PAST]


# --- Booking rules -------------------------------------------------------------------------


def test_a_free_slot_is_booked(world: World) -> None:
    reservation = world.book(world.afternoon, FRIDAY, phone="+5493515550401", notes="  cumple ")

    assert reservation.status == ReservationStatus.CONFIRMED
    assert reservation.source == ReservationSource.BOT
    assert reservation.created_by_phone == "+5493515550401"
    assert reservation.notes == "cumple"


def test_the_same_slot_cannot_be_booked_twice(world: World) -> None:
    world.book(world.afternoon, FRIDAY)

    with pytest.raises(BookingError) as error:
        world.book(world.afternoon, FRIDAY, world.other_unit)

    assert error.value.problems == [BookingProblem.TAKEN]
    assert not error.value.overridable
    world.book(world.night, FRIDAY, world.other_unit)  # another slot of that day is free


def test_the_database_rejects_two_confirmed_reservations(world: World) -> None:
    world.book(world.afternoon, FRIDAY)
    duplicate = Reservation(
        amenity_id=world.amenity.id,
        slot_id=world.afternoon.id,
        date=FRIDAY,
        unit_id=world.other_unit.id,
        source=ReservationSource.PANEL,
    )
    with pytest.raises(IntegrityError), world.session.begin_nested():
        world.session.add(duplicate)
        world.session.flush()


def test_a_cancelled_reservation_frees_the_slot(world: World) -> None:
    first = world.book(world.afternoon, FRIDAY)
    cancel(world.session, first, now=NOW, timezone=TZ, by_panel=True)

    second = world.book(world.afternoon, FRIDAY, world.other_unit)

    assert second.status == ReservationStatus.CONFIRMED


@pytest.mark.parametrize(
    ("day", "unit", "problem"),
    [
        (date(2026, 10, 8), "unit", BookingProblem.NO_SUCH_SLOT),  # a Thursday
        (FRIDAY, "stranger", BookingProblem.UNIT_NOT_IN_BUILDING),
        (date(2026, 10, 2), "unit", BookingProblem.PAST),  # last Friday
    ],
)
def test_structural_rules(world: World, day: date, unit: str, problem: BookingProblem) -> None:
    with pytest.raises(BookingError) as error:
        world.book(world.afternoon, day, getattr(world, unit))
    assert error.value.problems == [problem]
    assert not error.value.overridable


def test_inactive_amenity_or_slot(world: World) -> None:
    world.night.active = False
    assert world.problems(world.night, FRIDAY) == [BookingProblem.NO_SUCH_SLOT]
    world.amenity.active = False
    assert world.problems(world.afternoon, FRIDAY) == [BookingProblem.AMENITY_INACTIVE]


def test_minimum_and_maximum_advance(world: World) -> None:
    thursday_15 = datetime(2026, 10, 8, 15, tzinfo=CBA)  # 23 h before Friday 14:00
    assert world.problems(world.afternoon, FRIDAY, now=thursday_15) == [BookingProblem.TOO_SOON]
    thursday_14 = datetime(2026, 10, 8, 14, tzinfo=CBA)  # exactly 24 h before
    assert world.problems(world.afternoon, FRIDAY, now=thursday_14) == []

    assert world.problems(world.afternoon, date(2026, 11, 4) - timedelta(days=5)) == []  # 25 d
    assert world.problems(world.afternoon, date(2026, 11, 6)) == [BookingProblem.TOO_FAR]  # 32 d


def test_monthly_limit_per_unit(world: World) -> None:
    world.book(world.afternoon, FRIDAY)
    world.book(world.night, FRIDAY)
    # A cancelled one does not count; another unit has its own limit.
    third = world.book(world.afternoon, NEXT_FRIDAY, world.other_unit)
    cancel(world.session, third, now=NOW, timezone=TZ, by_panel=True)

    assert world.problems(world.afternoon, NEXT_FRIDAY) == [BookingProblem.MONTHLY_LIMIT]
    assert world.problems(world.afternoon, NEXT_FRIDAY, world.other_unit) == []
    assert world.problems(world.afternoon, date(2026, 11, 6), now=NOW + timedelta(days=5)) == []
    world.amenity.max_per_unit_per_month = None  # no limit
    assert world.problems(world.afternoon, NEXT_FRIDAY) == []


def _debt(world: World, unit: Unit, amount: str, up_to_date: bool, hours_ago: int) -> None:
    world.session.add(
        DebtSnapshot(
            unit_id=unit.id,
            fetched_at=datetime.now(UTC) - timedelta(hours=hours_ago),
            source=SyncKind.NIGHTLY,
            total_amount=Decimal(amount),
            is_up_to_date=up_to_date,
        )
    )
    world.session.flush()


def test_debtors_only_blocked_when_the_amenity_says_so(world: World) -> None:
    _debt(world, world.unit, "0", up_to_date=True, hours_ago=48)
    _debt(world, world.unit, "165060", up_to_date=False, hours_ago=1)  # the last one counts
    _debt(world, world.other_unit, "165060", up_to_date=False, hours_ago=48)
    _debt(world, world.other_unit, "0", up_to_date=True, hours_ago=1)

    assert world.problems(world.afternoon, FRIDAY) == []  # blocks_debtors is off
    world.amenity.blocks_debtors = True
    assert world.problems(world.afternoon, FRIDAY) == [BookingProblem.DEBTOR]
    assert world.problems(world.afternoon, FRIDAY, world.other_unit) == []  # paid since


def test_unit_without_debt_data_is_not_blocked(world: World) -> None:
    world.amenity.blocks_debtors = True
    assert world.problems(world.afternoon, FRIDAY) == []


def test_the_panel_can_override_policy_rules_only(world: World) -> None:
    soon = datetime(2026, 10, 9, 9, tzinfo=CBA)  # Friday 9:00, the slot is at 14:00

    with pytest.raises(BookingError) as error:
        world.book(world.afternoon, FRIDAY, now=soon, source=ReservationSource.PANEL)
    assert error.value.problems == [BookingProblem.TOO_SOON] and error.value.overridable

    # The bot never skips a rule.
    with pytest.raises(BookingError):
        world.book(world.afternoon, FRIDAY, now=soon, override={BookingProblem.TOO_SOON})

    override = {BookingProblem.TOO_SOON, BookingProblem.TAKEN}
    reservation = world.book(
        world.afternoon, FRIDAY, now=soon, source=ReservationSource.PANEL, override=override
    )
    assert reservation.source == ReservationSource.PANEL
    # TAKEN is not a policy rule: overriding it does nothing.
    with pytest.raises(BookingError) as error:
        world.book(
            world.afternoon,
            FRIDAY,
            world.other_unit,
            now=soon,
            source=ReservationSource.PANEL,
            override=override,
        )
    assert BookingProblem.TAKEN in error.value.problems


# --- Cancelling ----------------------------------------------------------------------------


def test_the_person_cancels_until_the_limit_the_panel_always(world: World) -> None:
    reservation = world.book(world.afternoon, FRIDAY)
    wednesday_15 = datetime(2026, 10, 7, 15, tzinfo=CBA)  # 47 h before

    with pytest.raises(BookingError) as error:
        cancel(world.session, reservation, now=wednesday_15, timezone=TZ, by_panel=False)
    assert error.value.problems == [BookingProblem.TOO_LATE_TO_CANCEL]
    assert "48 h" in str(error.value)

    wednesday_14 = datetime(2026, 10, 7, 14, tzinfo=CBA)  # exactly 48 h before
    cancel(world.session, reservation, now=wednesday_14, timezone=TZ, by_panel=False)
    assert reservation.status == ReservationStatus.CANCELLED
    assert reservation.cancelled_at == wednesday_14

    late = world.book(world.afternoon, FRIDAY, world.other_unit)
    cancel(world.session, late, now=wednesday_15, timezone=TZ, by_panel=True)
    assert late.status == ReservationStatus.CANCELLED
    with pytest.raises(BookingError) as error:
        cancel(world.session, late, now=wednesday_15, timezone=TZ, by_panel=True)
    assert error.value.problems == [BookingProblem.ALREADY_CANCELLED]


# --- Availability --------------------------------------------------------------------------


def test_availability_of_a_range(world: World) -> None:
    taken = world.book(world.night, FRIDAY)

    days = availability(
        world.session, world.amenity, date(2026, 10, 5), date(2026, 10, 18), timezone=TZ
    )

    assert [(d.day, d.slot.start_time, d.free) for d in days] == [
        (FRIDAY, time(14), True),
        (FRIDAY, time(20), False),
        (NEXT_FRIDAY, time(14), True),
        (NEXT_FRIDAY, time(20), True),
    ]
    assert days[1].reservation is taken

    friday_15 = datetime(2026, 10, 9, 15, tzinfo=CBA)
    free = free_slots(world.session, world.amenity, FRIDAY, NEXT_FRIDAY, now=friday_15, timezone=TZ)
    # Friday afternoon already started and Friday night is taken.
    assert [(d.day, d.slot.start_time) for d in free] == [
        (NEXT_FRIDAY, time(14)),
        (NEXT_FRIDAY, time(20)),
    ]
