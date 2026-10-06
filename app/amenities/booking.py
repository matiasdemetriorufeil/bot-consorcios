"""Booking rules of amenities (the SUM): one place for the admin panel and, later, the bot.

Times are local (the studio's timezone). A reservation is for a slot on the date the slot
starts; a slot whose end_time is not after its start_time ends the next day (20:00 to
02:00). The rules:

- the slot exists, is active and is of that date's weekday; the amenity is active; the unit is
  of the amenity's building;
- not in the past (the slot has not started);
- the slot is free (also guarded by a partial unique index: two confirmed reservations of the
  same slot and date cannot exist);
- at least min_advance_hours before the start, at most max_advance_days ahead;
- at most max_per_unit_per_month confirmed reservations per unit in that calendar month;
- if blocks_debtors, the unit's last stored debt must be up to date (no data: allowed).

The last four are POLICY_RULES: the panel may book anyway (override, audited); the bot never.
Cancelling: until cancel_until_hours before the start; the panel can always cancel.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    Amenity,
    AmenitySlot,
    Reservation,
    ReservationSource,
    ReservationStatus,
    Unit,
)
from app.db.queries import get_latest_debt


class BookingProblem(StrEnum):
    AMENITY_INACTIVE = "amenity_inactive"
    NO_SUCH_SLOT = "no_such_slot"
    UNIT_NOT_IN_BUILDING = "unit_not_in_building"
    PAST = "past"
    TAKEN = "taken"
    TOO_SOON = "too_soon"
    TOO_FAR = "too_far"
    MONTHLY_LIMIT = "monthly_limit"
    DEBTOR = "debtor"
    ALREADY_CANCELLED = "already_cancelled"
    TOO_LATE_TO_CANCEL = "too_late_to_cancel"


POLICY_RULES = frozenset(
    {
        BookingProblem.TOO_SOON,
        BookingProblem.TOO_FAR,
        BookingProblem.MONTHLY_LIMIT,
        BookingProblem.DEBTOR,
    }
)


class BookingError(Exception):
    """The reservation (or cancellation) breaks a rule. problems: every one found."""

    def __init__(self, problems: list[BookingProblem], messages: list[str]) -> None:
        super().__init__("; ".join(messages))
        self.problems = problems
        self.messages = messages

    @property
    def overridable(self) -> bool:
        """Only policy rules failed: the panel may book anyway."""
        return all(p in POLICY_RULES for p in self.problems)


def slot_start(day: date, slot: AmenitySlot, timezone: str) -> datetime:
    return datetime.combine(day, slot.start_time, tzinfo=ZoneInfo(timezone))


def slot_end(day: date, slot: AmenitySlot, timezone: str) -> datetime:
    end_day = day + timedelta(days=1) if slot.overnight else day
    return datetime.combine(end_day, slot.end_time, tzinfo=ZoneInfo(timezone))


def describe_slot(slot: AmenitySlot) -> str:
    """ "20:00 a 02:00 (del día siguiente)" or "12:00 a 16:00 · Almuerzo"."""
    text = f"{slot.start_time:%H:%M} a {slot.end_time:%H:%M}"
    if slot.overnight:
        text += " (del día siguiente)"
    return f"{text} · {slot.label}" if slot.label else text


# --- Availability ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SlotDay:
    """One slot on one date: free, or with its confirmed reservation. problems: why nobody
    can book it at the given moment (timing_problems, or the amenity is inactive); only
    filled when availability gets now."""

    day: date
    slot: AmenitySlot
    reservation: Reservation | None
    problems: tuple[BookingProblem, ...] = ()

    @property
    def free(self) -> bool:
        return self.reservation is None

    @property
    def bookable(self) -> bool:
        return self.free and not self.problems


def availability(
    session: Session,
    amenity: Amenity,
    first: date,
    last: date,
    *,
    now: datetime | None = None,
    timezone: str,
) -> list[SlotDay]:
    """Every active slot of every date from first to last (inclusive), with its confirmed
    reservation if any, in date and start order. With now, each one also says why it cannot
    be booked at that moment (SlotDay.problems: past, too soon, too far, amenity inactive)."""
    slots = [s for s in amenity.slots if s.active]
    booked = {
        (r.slot_id, r.date): r
        for r in session.scalars(
            select(Reservation).where(
                Reservation.amenity_id == amenity.id,
                Reservation.status == ReservationStatus.CONFIRMED,
                Reservation.date.between(first, last),
            )
        )
    }
    result: list[SlotDay] = []
    day = first
    while day <= last:
        for slot in sorted(slots, key=lambda s: s.start_time):
            if slot.weekday != day.weekday():
                continue
            problems: list[BookingProblem] = []
            if now is not None:
                if not amenity.active:
                    problems.append(BookingProblem.AMENITY_INACTIVE)
                problems += timing_problems(amenity, slot, day, now=now, timezone=timezone)
            result.append(SlotDay(day, slot, booked.get((slot.id, day)), tuple(problems)))
        day += timedelta(days=1)
    return result


def free_slots(
    session: Session, amenity: Amenity, first: date, last: date, *, now: datetime, timezone: str
) -> list[SlotDay]:
    """The slots that can be booked from first to last at that moment (not taken, not
    started, inside the advance window, amenity active)."""
    days = availability(session, amenity, first, last, now=now, timezone=timezone)
    return [d for d in days if d.bookable]


# --- Rules ----------------------------------------------------------------------------------


_MESSAGES = {
    BookingProblem.AMENITY_INACTIVE: "El {name} no está habilitado para reservas.",
    BookingProblem.NO_SUCH_SLOT: "Ese turno no existe para ese día.",
    BookingProblem.UNIT_NOT_IN_BUILDING: "La unidad no es de este edificio.",
    BookingProblem.PAST: "Ese turno ya empezó o ya pasó.",
    BookingProblem.TAKEN: "Ese turno ya está reservado.",
    BookingProblem.TOO_SOON: "Hay que reservar con al menos {hours} h de anticipación.",
    BookingProblem.TOO_FAR: "Solo se puede reservar hasta {days} días antes.",
    BookingProblem.MONTHLY_LIMIT: "La unidad ya tiene {limit} reserva(s) ese mes (es el máximo).",
    BookingProblem.DEBTOR: "La unidad figura con deuda: no puede reservar el {name}.",
    BookingProblem.ALREADY_CANCELLED: "La reserva ya estaba cancelada.",
    BookingProblem.TOO_LATE_TO_CANCEL: "Solo se puede cancelar hasta {hours} h antes del turno.",
}


def _message(problem: BookingProblem, amenity: Amenity) -> str:
    return _MESSAGES[problem].format(
        name=amenity.name,
        hours=(
            amenity.cancel_until_hours
            if problem == BookingProblem.TOO_LATE_TO_CANCEL
            else amenity.min_advance_hours
        ),
        days=amenity.max_advance_days,
        limit=amenity.max_per_unit_per_month,
    )


def problem_message(problem: BookingProblem, amenity: Amenity) -> str:
    """What to tell (in Spanish) about a problem of this amenity."""
    return _message(problem, amenity)


def _error(problems: list[BookingProblem], amenity: Amenity) -> BookingError:
    return BookingError(problems, [_message(p, amenity) for p in problems])


def _is_taken(session: Session, slot_id: int, day: date) -> bool:
    return (
        session.scalar(
            select(Reservation.id).where(
                Reservation.slot_id == slot_id,
                Reservation.date == day,
                Reservation.status == ReservationStatus.CONFIRMED,
            )
        )
        is not None
    )


def _month_count(session: Session, amenity_id: int, unit_id: int, day: date) -> int:
    first = day.replace(day=1)
    next_month = (first + timedelta(days=32)).replace(day=1)
    return session.scalar(
        select(func.count(Reservation.id)).where(
            Reservation.amenity_id == amenity_id,
            Reservation.unit_id == unit_id,
            Reservation.status == ReservationStatus.CONFIRMED,
            Reservation.date >= first,
            Reservation.date < next_month,
        )
    )


def _is_debtor(session: Session, unit_id: int) -> bool:
    """The last stored debt says the unit owes money. No data: not a debtor."""
    snapshot = get_latest_debt(session, unit_id)
    return snapshot is not None and not snapshot.is_up_to_date and snapshot.total_amount > 0


def timing_problems(
    amenity: Amenity, slot: AmenitySlot, day: date, *, now: datetime, timezone: str
) -> list[BookingProblem]:
    """Why nobody can book the slot on that day at that moment because of time: it already
    started (PAST, alone), or it is inside min_advance_hours or beyond max_advance_days."""
    start = slot_start(day, slot, timezone)
    if start <= now:
        return [BookingProblem.PAST]
    problems = []
    if start - now < timedelta(hours=amenity.min_advance_hours):
        problems.append(BookingProblem.TOO_SOON)
    today = now.astimezone(ZoneInfo(timezone)).date()
    if day > today + timedelta(days=amenity.max_advance_days):
        problems.append(BookingProblem.TOO_FAR)
    return problems


def check_booking(
    session: Session,
    amenity: Amenity,
    slot: AmenitySlot | None,
    day: date,
    unit: Unit | None,
    *,
    now: datetime,
    timezone: str,
) -> list[BookingProblem]:
    """Every rule the reservation would break (empty: it can be made)."""
    problems: list[BookingProblem] = []
    if not amenity.active:
        problems.append(BookingProblem.AMENITY_INACTIVE)
    if (
        slot is None
        or slot.amenity_id != amenity.id
        or not slot.active
        or slot.weekday != day.weekday()
    ):
        problems.append(BookingProblem.NO_SUCH_SLOT)
    if unit is None or unit.building_id != amenity.building_id:
        problems.append(BookingProblem.UNIT_NOT_IN_BUILDING)
    if problems or slot is None or unit is None:
        return problems

    timing = timing_problems(amenity, slot, day, now=now, timezone=timezone)
    if timing == [BookingProblem.PAST]:
        return timing
    if _is_taken(session, slot.id, day):
        problems.append(BookingProblem.TAKEN)
    problems += timing
    limit = amenity.max_per_unit_per_month
    if limit is not None and _month_count(session, amenity.id, unit.id, day) >= limit:
        problems.append(BookingProblem.MONTHLY_LIMIT)
    if amenity.blocks_debtors and _is_debtor(session, unit.id):
        problems.append(BookingProblem.DEBTOR)
    return problems


def book(
    session: Session,
    amenity: Amenity,
    slot_id: int,
    day: date,
    unit_id: int,
    *,
    now: datetime,
    timezone: str,
    source: ReservationSource,
    phone: str | None = None,
    notes: str | None = None,
    override: Collection[BookingProblem] = (),
) -> Reservation:
    """Create a confirmed reservation (committed by the caller: flush only). override: policy
    rules the panel chose to skip (never structural ones). BookingError if a rule fails."""
    slot = session.get(AmenitySlot, slot_id)
    unit = session.get(Unit, unit_id)
    problems = check_booking(session, amenity, slot, day, unit, now=now, timezone=timezone)
    skipped = set(override) & POLICY_RULES if source == ReservationSource.PANEL else set()
    remaining = [p for p in problems if p not in skipped]
    if remaining:
        raise _error(remaining, amenity)
    reservation = Reservation(
        amenity_id=amenity.id,
        slot_id=slot_id,
        date=day,
        unit_id=unit_id,
        status=ReservationStatus.CONFIRMED,
        source=source,
        created_by_phone=phone,
        notes=(notes or "").strip() or None,
    )
    try:
        with session.begin_nested():
            session.add(reservation)
            session.flush()
    except IntegrityError as exc:
        # Someone else took it between the check and the insert.
        raise _error([BookingProblem.TAKEN], amenity) from exc
    return reservation


def cancel(
    session: Session,
    reservation: Reservation,
    *,
    now: datetime,
    timezone: str,
    by_panel: bool,
) -> Reservation:
    """Cancel a confirmed reservation (flush only). The person: until cancel_until_hours
    before the start; the panel: always. BookingError otherwise."""
    amenity = reservation.amenity
    if reservation.status == ReservationStatus.CANCELLED:
        raise _error([BookingProblem.ALREADY_CANCELLED], amenity)
    start = slot_start(reservation.date, reservation.slot, timezone)
    if not by_panel and start - now < timedelta(hours=amenity.cancel_until_hours):
        raise _error([BookingProblem.TOO_LATE_TO_CANCEL], amenity)
    reservation.status = ReservationStatus.CANCELLED
    reservation.cancelled_at = now
    session.flush()
    return reservation
