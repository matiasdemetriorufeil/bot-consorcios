"""The agent's SUM tools: availability, booking, the person's reservations and cancelling.

Every rule comes from app.amenities.booking (the same one the admin panel uses): the model
only asks. Buildings are named as the person wrote them (or are the person's own), never by
id, and a slot is its start time on a date ("20:00" on 2026-10-09): both still mean the same
in the next message, when the history only keeps texts.

Booking and cancelling go in two steps. Without confirmation the code builds a fixed summary
(SUM, day, slot, unit) and ends the turn with it and the buttons "Sí, reservar" / "No" (or
"Sí, cancelar" / "No"). It only books or cancels when the person's current message is a yes
AND the bot's previous message is that very summary: the model cannot skip the confirmation.

Availability is public. Booking needs a verified phone with a unit (owner or tenant) in that
building, and bot_booking_enabled on the amenity; otherwise the bot offers a person.
"""

import re
import unicodedata
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.amenities.booking import (
    BookingError,
    BookingProblem,
    book,
    cancel,
    check_booking,
    describe_slot,
    free_slots,
    problem_message,
    slot_end,
    slot_start,
)
from app.bot.choices import Choice, Offer
from app.bot.identity import Identity, identify_by_phone
from app.bot.unit_search import display_building_name, search_building
from app.db.models import (
    Amenity,
    AmenitySlot,
    Building,
    Reservation,
    ReservationSource,
    ReservationStatus,
    Unit,
)

if TYPE_CHECKING:
    from app.bot.tools import ToolContext

MAX_DAYS = 14  # per availability query
MAX_FREE_LISTED = 40
BOOK_CHOICES = (Choice("Sí, reservar", "Sí, reservar"), Choice("No", "No"))
CANCEL_CHOICES = (Choice("Sí, cancelar", "Sí, cancelar"), Choice("No", "No"))
# What counts as "yes" to a summary (normalized: lower case, no accents nor punctuation).
YES_BOOK = frozenset(
    {"si reservar", "si", "si reservalo", "si dale", "dale", "confirmo", "si confirmo"}
)
YES_CANCEL = frozenset(
    {"si cancelar", "si", "si cancelala", "si dale", "dale", "confirmo", "si confirmo"}
)
_DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

OFFER_HANDOFF = (
    "Ofrecé pasarlo con una persona del estudio con offer_choices ('Sí, pasame' / 'No, gracias')."
)
SUMMARY_SENT = "El resumen con los botones ya le sale a la persona: no escribas nada más."


# --- Helpers --------------------------------------------------------------------------------


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z ]+", " ", plain).split())


def _confirmed(ctx: "ToolContext", summary: str, yes: frozenset[str]) -> bool:
    """The person said yes now, to exactly this summary (the bot's previous message)."""
    return _normalize(ctx.user_text) in yes and summary in ctx.last_bot_text


def _now(ctx: "ToolContext") -> datetime:
    return ctx.now or datetime.now(UTC)


def _today(ctx: "ToolContext") -> date:
    return _now(ctx).astimezone(ZoneInfo(ctx.timezone)).date()


def _day_text(day: date) -> str:
    """ "viernes 09/10/2026"."""
    return f"{_DAYS[day.weekday()]} {day:%d/%m/%Y}"


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _time(value: str) -> time | None:
    try:
        return time.fromisoformat(value.strip().zfill(5))
    except ValueError:
        return None


def _amenity_of(ctx: "ToolContext", building: Building) -> Amenity | None:
    return ctx.session.scalar(
        select(Amenity).where(Amenity.building_id == building.id).order_by(Amenity.id).limit(1)
    )


def _limits(amenity: Amenity) -> dict[str, Any]:
    limits: dict[str, Any] = {
        "min_advance_hours": amenity.min_advance_hours,
        "max_advance_days": amenity.max_advance_days,
        "cancel_until_hours": amenity.cancel_until_hours,
    }
    if amenity.max_per_unit_per_month is not None:
        limits["max_per_unit_per_month"] = amenity.max_per_unit_per_month
    if amenity.blocks_debtors:
        limits["debtors_cannot_book"] = True
    return limits


def _own_buildings(who: Identity) -> dict[int, str]:
    own: dict[int, str] = {}
    for u in who.units:
        own.setdefault(u.building_id, u.building_name)
    return own


def _resolve_building(
    ctx: "ToolContext", building: str, who: Identity, *, own_only: bool
) -> tuple[Building | None, dict[str, Any] | None]:
    """The building asked about, or the result to return. Named: tolerant search (with
    own_only, it must be one of the person's). Not named: the person's only building."""
    from app.bot.tools import named_building  # noqa: PLC0415 (tools imports this module)

    own = _own_buildings(who)
    building = named_building(ctx, building)
    if building.strip():
        found = search_building(ctx.session, building)
        if own_only:
            found = [b for b in found if b.id in own] or found
        if len(found) > 1:
            return None, {
                "status": "ambiguous_building",
                "buildings": [display_building_name(b.name) for b in found],
                "next_step": "Preguntá cuál de estos edificios es.",
            }
        if not found:
            return None, {
                "status": "building_not_found",
                "next_step": "No encontré ese edificio: pedí que lo confirme.",
            }
        if own_only and found[0].id not in own:
            return None, {
                "status": "denied",
                "reason": "not_your_building",
                "next_step": "Solo se reserva para una unidad propia de ese edificio. No reserves.",
            }
        return found[0], None
    if len(own) == 1:
        return ctx.session.get(Building, next(iter(own))), None
    if own:
        return None, {
            "status": "which_building",
            "buildings": [display_building_name(n) for n in own.values()],
            "next_step": "Preguntá de qué edificio (offer_choices con los nombres).",
        }
    return None, {"status": "need_building", "next_step": "Preguntá de qué edificio se trata."}


def _no_sum(building: Building, amenity: Amenity | None) -> dict[str, Any] | None:
    name = display_building_name(building.name)
    if amenity is None:
        return {
            "status": "no_sum",
            "building": name,
            "next_step": "El estudio no cargó el SUM de este edificio: decí que no tenés ese "
            f"dato. {OFFER_HANDOFF}",
        }
    if not amenity.active:
        return {
            "status": "sum_not_bookable",
            "building": name,
            "next_step": f"El SUM no está habilitado para reservas. {OFFER_HANDOFF}",
        }
    return None


def _building_and_sum(
    ctx: "ToolContext", building: str, who: Identity, *, own_only: bool
) -> tuple[Building, Amenity] | dict[str, Any]:
    """The building and its active SUM, or the result to return instead."""
    found, problem = _resolve_building(ctx, building, who, own_only=own_only)
    if problem is not None or found is None:
        return problem or {"status": "need_building"}
    amenity = _amenity_of(ctx, found)
    if (missing := _no_sum(found, amenity)) is not None or amenity is None:
        return missing or {"status": "no_sum"}
    return found, amenity


def _slot_item(day: date, slot: AmenitySlot) -> dict[str, str]:
    return {
        "date": day.isoformat(),
        "day": _day_text(day),
        "slot": f"{slot.start_time:%H:%M}",
        "time": describe_slot(slot),
    }


# --- Availability ---------------------------------------------------------------------------


def sum_availability(
    ctx: "ToolContext", date_from: str, date_to: str = "", building: str = ""
) -> dict[str, Any]:
    """Public: free slots and the rules of the SUM of a building."""
    first, last = _date(date_from), _date(date_to) if date_to.strip() else _date(date_from)
    if first is None or last is None:
        return {"status": "error", "error": "fechas en formato AAAA-MM-DD"}
    today = _today(ctx)
    first = max(first, today)
    last = max(last, first)
    cut = last > first + timedelta(days=MAX_DAYS - 1)
    last = min(last, first + timedelta(days=MAX_DAYS - 1))
    who = identify_by_phone(ctx.session, ctx.phone)
    resolved = _building_and_sum(ctx, building, who, own_only=False)
    if isinstance(resolved, dict):
        return resolved
    found, amenity = resolved
    free = free_slots(ctx.session, amenity, first, last, now=_now(ctx), timezone=ctx.timezone)
    result: dict[str, Any] = {
        "status": "ok" if free else "none_free",
        "building": display_building_name(found.name),
        "sum": amenity.name,
        "from": first.isoformat(),
        "to": last.isoformat(),
        "free": [_slot_item(d.day, d.slot) for d in free[:MAX_FREE_LISTED]],
        "rules_text": amenity.rules_text or None,
        "limits": _limits(amenity),
        "bot_can_book": amenity.bot_booking_enabled,
    }
    if cut:
        result["note"] = f"Consulta recortada a {MAX_DAYS} días."
    if amenity.bot_booking_enabled:
        result["next_step"] = (
            "Para reservar, book_sum con date y slot (la hora de inicio) de free. Las reglas: "
            "solo lo que dice rules_text y limits."
        )
    else:
        result["next_step"] = (
            "En este edificio el bot no reserva: informá disponibilidad y reglas y, si quiere "
            f"reservar, no uses book_sum. {OFFER_HANDOFF}"
        )
    return result


# --- Booking --------------------------------------------------------------------------------


def _not_verified() -> dict[str, Any]:
    return {
        "status": "denied",
        "reason": "not_verified",
        "next_step": "Para reservar o ver sus reservas, el número tiene que estar verificado "
        "con una unidad del edificio. Ofrecé verificarlo (find_unit y, si acepta, "
        "start_email_verification). La disponibilidad y las reglas sí las podés informar.",
    }


def book_sum(
    ctx: "ToolContext", date: str, slot: str, building: str = "", unit_id: int | None = None
) -> dict[str, Any]:
    """Book a slot for one of the person's units, after the summary was confirmed."""
    who = identify_by_phone(ctx.session, ctx.phone)
    if not who.known or not who.units:
        return _not_verified()
    resolved = _building_and_sum(ctx, building, who, own_only=True)
    if isinstance(resolved, dict):
        return resolved
    found, amenity = resolved
    building_name = display_building_name(found.name)
    if not amenity.bot_booking_enabled:
        return {
            "status": "bot_booking_disabled",
            "building": building_name,
            "next_step": "En este edificio las reservas del SUM las hace una persona del "
            f"estudio: no reserves. {OFFER_HANDOFF}",
        }
    units = [u for u in who.units if u.building_id == found.id]
    if unit_id is not None:
        units = [u for u in units if u.unit_id == unit_id]
        if not units:
            return {
                "status": "error",
                "reason": "unit_not_yours",
                "next_step": "Ese unit_id no es de la persona en este edificio: preguntá a "
                "nombre de cuál de sus unidades.",
            }
    elif len(units) > 1:
        return {
            "status": "which_unit",
            "units": [{"unit_id": u.unit_id, "unit": u.unit_label} for u in units],
            "next_step": "Preguntá a nombre de cuál unidad reserva (offer_choices con las "
            "etiquetas) y volvé a llamar book_sum con su unit_id.",
        }
    day, start = _date(date), _time(slot)
    if day is None or start is None:
        return {"status": "error", "error": "date AAAA-MM-DD y slot HH:MM (hora de inicio)"}
    chosen = next(
        (
            s
            for s in amenity.slots
            if s.active and s.weekday == day.weekday() and s.start_time == start
        ),
        None,
    )
    if chosen is None:
        that_day = [
            describe_slot(s) for s in amenity.slots if s.active and s.weekday == day.weekday()
        ]
        return {
            "status": "no_such_slot",
            "day": _day_text(day),
            "slots_that_day": that_day,
            "next_step": "Ese turno no existe ese día: ofrecé los turnos de slots_that_day o "
            "consultá sum_availability.",
        }
    unit = ctx.session.get(Unit, units[0].unit_id)
    now = _now(ctx)
    problems = check_booking(
        ctx.session, amenity, chosen, day, unit, now=now, timezone=ctx.timezone
    )
    if problems:
        result: dict[str, Any] = {
            "status": "not_possible",
            "reasons": [p.value for p in problems],
            "say": " ".join(problem_message(p, amenity) for p in problems),
        }
        if BookingProblem.TAKEN in problems:
            result["next_step"] = (
                "Decí que ese turno ya está reservado y ofrecé otros libres (sum_availability)."
            )
        else:
            result["next_step"] = "Decí el texto de say. Si quiere, ofrecé otra fecha."
        return result
    summary = (
        f"Reserva del {amenity.name} de {building_name}:\n"
        f"• Día: {_day_text(day)}\n"
        f"• Turno: {describe_slot(chosen)}\n"
        f"• Unidad: {unit.label}"
    )
    if not _confirmed(ctx, summary, YES_BOOK):
        return _ask(ctx, f"{summary}\n¿Confirmás la reserva?", BOOK_CHOICES)
    try:
        reservation = book(
            ctx.session,
            amenity,
            chosen.id,
            day,
            unit.id,
            now=now,
            timezone=ctx.timezone,
            source=ReservationSource.BOT,
            phone=ctx.e164,
        )
    except BookingError as exc:
        return {
            "status": "not_possible",
            "reasons": [p.value for p in exc.problems],
            "say": str(exc),
            "next_step": "Decí el texto de say y ofrecé otros turnos (sum_availability).",
        }
    ctx.log(
        "sum_reservation_created",
        reservation_id=reservation.id,
        amenity_id=amenity.id,
        unit_id=unit.id,
        slot_id=chosen.id,
        date=day.isoformat(),
    )
    return {
        "status": "booked",
        "reservation": summary,
        "rules_text": amenity.rules_text or None,
        "cancel_until_hours": amenity.cancel_until_hours,
        "next_step": "Confirmá la reserva en una línea y repetí las reglas importantes de "
        "rules_text (música, limpieza, costo...) tal como están: no agregues nada que no diga. "
        "Si rules_text está vacío, no menciones reglas.",
    }


def _ask(ctx: "ToolContext", text: str, choices: tuple[Choice, ...]) -> dict[str, Any]:
    """Ends the turn with the summary and the buttons (built by the code)."""
    if ctx.handoff is not None or ctx.offer is not None:
        return {
            "status": "error",
            "reason": "cannot_ask_now",
            "next_step": "En este mensaje ya derivaste u ofreciste opciones: no reserves ni "
            "canceles ahora.",
        }
    if ctx.first_message:  # the summary is the whole reply: greet as with a debt message
        text = f"{ctx.greeting}\n\n{text}"
    ctx.offer = Offer(text, choices)
    return {"status": "confirmation_requested", "next_step": SUMMARY_SENT}


# --- The person's reservations -------------------------------------------------------------


def _own_reservations(ctx: "ToolContext", who: Identity) -> list[Reservation]:
    """Confirmed reservations of the person's units that have not ended."""
    unit_ids = [u.unit_id for u in who.units]
    if not unit_ids:
        return []
    now = _now(ctx)
    rows = ctx.session.scalars(
        select(Reservation)
        .where(
            Reservation.unit_id.in_(unit_ids),
            Reservation.status == ReservationStatus.CONFIRMED,
            Reservation.date >= _today(ctx) - timedelta(days=1),
        )
        .order_by(Reservation.date, Reservation.id)
    )
    return [r for r in rows if slot_end(r.date, r.slot, ctx.timezone) > now]


def _can_cancel(ctx: "ToolContext", reservation: Reservation) -> bool:
    start = slot_start(reservation.date, reservation.slot, ctx.timezone)
    return start - _now(ctx) >= timedelta(hours=reservation.amenity.cancel_until_hours)


def my_sum_reservations(ctx: "ToolContext") -> dict[str, Any]:
    who = identify_by_phone(ctx.session, ctx.phone)
    if not who.known or not who.units:
        return _not_verified()
    reservations = _own_reservations(ctx, who)
    ctx.offered_reservation_ids.update(r.id for r in reservations)
    items = [
        {
            "reservation_id": r.id,
            "building": display_building_name(r.amenity.building.name),
            "sum": r.amenity.name,
            "day": _day_text(r.date),
            "time": describe_slot(r.slot),
            "unit": r.unit.label,
            "can_cancel": _can_cancel(ctx, r),
            "cancel_until_hours": r.amenity.cancel_until_hours,
        }
        for r in reservations
    ]
    return {
        "status": "ok" if items else "none",
        "reservations": items,
        "next_step": "Para cancelar, cancel_sum_reservation con su reservation_id. Si "
        "can_cancel es false, ya pasó el plazo (cancel_until_hours antes del turno): decilo y "
        f"no la canceles. {OFFER_HANDOFF}",
    }


def cancel_sum_reservation(ctx: "ToolContext", reservation_id: int) -> dict[str, Any]:
    who = identify_by_phone(ctx.session, ctx.phone)
    if not who.known or not who.units:
        return _not_verified()
    if reservation_id not in ctx.offered_reservation_ids:
        return {
            "status": "error",
            "reason": "reservation_not_confirmed",
            "next_step": "Ese reservation_id no lo devolvió my_sum_reservations en este mensaje: "
            "llamala y usá el que devuelva. No se lo cuentes a la persona.",
        }
    reservation = ctx.session.get(Reservation, reservation_id)
    own = {u.unit_id for u in who.units}
    if reservation is None or reservation.unit_id not in own:
        return {"status": "denied", "reason": "not_yours"}
    if reservation.status != ReservationStatus.CONFIRMED:
        return {"status": "already_cancelled"}
    amenity = reservation.amenity
    if not _can_cancel(ctx, reservation):
        return {
            "status": "not_possible",
            "reasons": [BookingProblem.TOO_LATE_TO_CANCEL.value],
            "say": problem_message(BookingProblem.TOO_LATE_TO_CANCEL, amenity),
            "next_step": f"Decí el texto de say y no la canceles. {OFFER_HANDOFF}",
        }
    summary = (
        f"Cancelar la reserva del {amenity.name} de "
        f"{display_building_name(amenity.building.name)}:\n"
        f"• Día: {_day_text(reservation.date)}\n"
        f"• Turno: {describe_slot(reservation.slot)}\n"
        f"• Unidad: {reservation.unit.label}"
    )
    if not _confirmed(ctx, summary, YES_CANCEL):
        return _ask(ctx, f"{summary}\n¿Confirmás la cancelación?", CANCEL_CHOICES)
    try:
        cancel(ctx.session, reservation, now=_now(ctx), timezone=ctx.timezone, by_panel=False)
    except BookingError as exc:
        return {"status": "not_possible", "say": str(exc), "next_step": OFFER_HANDOFF}
    ctx.log(
        "sum_reservation_cancelled",
        reservation_id=reservation.id,
        amenity_id=amenity.id,
        unit_id=reservation.unit_id,
    )
    return {
        "status": "cancelled",
        "reservation": summary.replace("Cancelar la reserva", "Reserva cancelada", 1),
        "next_step": "Confirmá la cancelación en una línea: el turno quedó libre.",
    }
