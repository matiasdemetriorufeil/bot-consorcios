"""The agent's SUM tools (app.bot.amenity_tools) against the Postgres test database: who can
book, the two-step confirmation, cancelling and availability. All data is invented."""

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot.agent import Agent
from app.bot.choices import with_options
from app.bot.tools import ToolContext, run_tool
from app.config import Settings
from app.db.models import (
    Amenity,
    AmenitySlot,
    BotEvent,
    PersonRole,
    Reservation,
    ReservationSource,
    ReservationStatus,
)
from app.llm import AssistantMessage, UserMessage
from tests.bot import factories as f
from tests.llm.fakes import PROVIDERS, Call, Say, last_tool_result, scripted_provider

TZ = "America/Argentina/Cordoba"
CBA = ZoneInfo(TZ)
NOW = datetime(2026, 10, 5, 10, 0, tzinfo=CBA)  # Monday
FRIDAY = "2026-10-09"
OWNER = "+5493515550101"
TENANT = "+5493515550202"
TWO_UNITS = "+5493515550303"
OTHER_BUILDING_OWNER = "+5493515550404"
UNKNOWN = "+5493515550999"
RULES = "Música hasta la 1. Dejar el salón limpio y la basura en el contenedor."


@dataclass
class World:
    session: Session
    amenity: Amenity
    night: AmenitySlot  # Fridays 20:00 to 02:00
    unit_id: int
    second_unit_id: int  # of TWO_UNITS, with a third one
    third_unit_id: int

    def ctx(self, phone: str, user_text: str = "", last_bot_text: str = "") -> ToolContext:
        return ToolContext(
            session=self.session,
            phone=phone,
            refresh_debt=lambda unit_id: None,  # type: ignore[arg-type,return-value]
            now=NOW,
            user_text=user_text,
            last_bot_text=last_bot_text,
        )

    def reservations(self) -> list[Reservation]:
        self.session.expire_all()
        return list(self.session.scalars(select(Reservation).order_by(Reservation.id)))


@pytest.fixture
def world(db_session: Session) -> World:
    rodas = f.building(db_session, "031 RODAS II")
    amenity = Amenity(building_id=rodas.id, bot_booking_enabled=True, rules_text=RULES)
    night = AmenitySlot(weekday=4, start_time=time(20), end_time=time(2), label="Noche")
    noon = AmenitySlot(weekday=5, start_time=time(12), end_time=time(16))
    amenity.slots = [night, noon]
    db_session.add(amenity)
    unit = f.unit(db_session, rodas, "04-C")
    f.link(db_session, unit, f.person(db_session, "Ana Ficticia", phone=OWNER))
    f.link(
        db_session, unit, f.person(db_session, "Tito Inventado", phone=TENANT), PersonRole.TENANT
    )
    second, third = f.unit(db_session, rodas, "05-A"), f.unit(db_session, rodas, "COC.2")
    both = f.person(db_session, "Dos Unidades", phone=TWO_UNITS)
    f.link(db_session, second, both)
    f.link(db_session, third, both)
    sol = f.building(db_session, "103 TORRE DEL SOL")
    db_session.add(
        Amenity(
            building_id=sol.id,
            bot_booking_enabled=False,
            slots=[AmenitySlot(weekday=4, start_time=time(20), end_time=time(2))],
        )
    )
    f.link(
        db_session,
        f.unit(db_session, sol, "01-B"),
        f.person(db_session, "Otro Edificio", phone=OTHER_BUILDING_OWNER),
    )
    f.building(db_session, "104 LOS ALGARROBOS")  # no SUM
    db_session.commit()
    return World(db_session, amenity, night, unit.id, second.id, third.id)


def _book(world: World, phone: str, **args: Any) -> tuple[dict[str, Any], ToolContext]:
    """First call: returns the result and the context (with the summary offered)."""
    ctx = world.ctx(phone)
    return run_tool(ctx, "book_sum", {"date": FRIDAY, "slot": "20:00", **args}), ctx


def _confirm(world: World, phone: str, asked: ToolContext, answer: str, **args: Any) -> Any:
    """Second message: the person answers the summary the bot sent."""
    assert asked.offer is not None
    shown = with_options(asked.offer.text, asked.offer.titles)
    ctx = world.ctx(phone, user_text=answer, last_bot_text=shown)
    return run_tool(ctx, "book_sum", {"date": FRIDAY, "slot": "20:00", **args}), ctx


# --- Availability (public) -------------------------------------------------------------------


def test_availability_is_public_and_brings_rules_and_limits(world: World) -> None:
    result = run_tool(
        world.ctx(UNKNOWN),
        "sum_availability",
        {"date_from": "2026-10-09", "date_to": "2026-10-10", "building": "Rodas 2"},
    )

    assert result["status"] == "ok" and result["building"] == "RODAS II"
    assert result["free"] == [
        {
            "date": "2026-10-09",
            "day": "viernes 09/10/2026",
            "slot": "20:00",
            "time": "20:00 a 02:00 (del día siguiente) · Noche",
        },
        {
            "date": "2026-10-10",
            "day": "sábado 10/10/2026",
            "slot": "12:00",
            "time": "12:00 a 16:00",
        },
    ]
    assert result["rules_text"] == RULES and result["bot_can_book"] is True
    assert result["limits"]["cancel_until_hours"] == 48


def test_availability_of_the_own_building_from_today_up_to_14_days(world: World) -> None:
    result = run_tool(
        world.ctx(OWNER), "sum_availability", {"date_from": "2026-09-01", "date_to": "2026-12-31"}
    )

    assert result["from"] == "2026-10-05" and result["to"] == "2026-10-18"  # past: from today
    assert "14 días" in result["note"]
    assert [i["date"] for i in result["free"]] == [
        "2026-10-09",
        "2026-10-10",
        "2026-10-16",
        "2026-10-17",
    ]


def test_availability_without_a_sum_or_with_the_bot_not_booking(world: World) -> None:
    ctx = world.ctx(UNKNOWN)
    no_sum = run_tool(ctx, "sum_availability", {"date_from": FRIDAY, "building": "Algarrobos"})
    assert no_sum["status"] == "no_sum" and "Sí, pasame" in no_sum["next_step"]

    sol = run_tool(ctx, "sum_availability", {"date_from": FRIDAY, "building": "Torre del Sol"})
    assert sol["status"] == "ok" and sol["bot_can_book"] is False
    assert "no uses book_sum" in sol["next_step"]

    world.amenity.active = False
    world.session.commit()
    off = run_tool(ctx, "sum_availability", {"date_from": FRIDAY, "building": "Rodas 2"})
    assert off["status"] == "sum_not_bookable"


# --- Booking: who ---------------------------------------------------------------------------


def test_an_unverified_number_cannot_book(world: World) -> None:
    result, ctx = _book(world, UNKNOWN, building="Rodas 2")

    assert result["status"] == "denied" and result["reason"] == "not_verified"
    assert ctx.offer is None and world.reservations() == []


def test_only_for_a_building_of_the_person(world: World) -> None:
    result, _ = _book(world, OWNER, building="Torre del Sol")
    assert result["reason"] == "not_your_building"


def test_bot_booking_disabled_offers_a_person(world: World) -> None:
    result, ctx = _book(world, OTHER_BUILDING_OWNER)

    assert result["status"] == "bot_booking_disabled" and ctx.offer is None
    assert "Sí, pasame" in result["next_step"]


def test_a_tenant_can_book(world: World) -> None:
    result, asked = _book(world, TENANT)
    assert result["status"] == "confirmation_requested"

    booked, _ = _confirm(world, TENANT, asked, "Sí, reservar")

    assert booked["status"] == "booked"
    [reservation] = world.reservations()
    assert reservation.unit_id == world.unit_id and reservation.created_by_phone == TENANT


def test_with_several_units_it_asks_which_one(world: World) -> None:
    result, ctx = _book(world, TWO_UNITS)

    assert result["status"] == "which_unit" and ctx.offer is None
    assert {u["unit"] for u in result["units"]} == {"05-A", "COC.2"}

    chosen, asked = _book(world, TWO_UNITS, unit_id=world.third_unit_id)
    assert chosen["status"] == "confirmation_requested"
    assert "• Unidad: COC.2" in asked.offer.text  # type: ignore[union-attr]
    # Someone else's unit: refused.
    other, _ = _book(world, TWO_UNITS, unit_id=world.unit_id)
    assert other["status"] == "error"


# --- Booking: the confirmation ----------------------------------------------------------------


def test_first_call_only_shows_the_summary_with_buttons(world: World) -> None:
    result, ctx = _book(world, OWNER)

    assert result["status"] == "confirmation_requested"
    assert ctx.offer is not None
    assert ctx.offer.text == (
        "Reserva del SUM de RODAS II:\n"
        "• Día: viernes 09/10/2026\n"
        "• Turno: 20:00 a 02:00 (del día siguiente) · Noche\n"
        "• Unidad: 04-C\n"
        "¿Confirmás la reserva?"
    )
    assert ctx.offer.titles == ["Sí, reservar", "No"]
    assert world.reservations() == []


@pytest.mark.parametrize("answer", ["Sí, reservar", "si", "Dale!", "confirmo"])
def test_confirming_the_summary_books_it(world: World, answer: str) -> None:
    _, asked = _book(world, OWNER)

    result, _ = _confirm(world, OWNER, asked, answer)

    assert result["status"] == "booked" and result["rules_text"] == RULES
    assert "repetí las reglas importantes de rules_text" in result["next_step"]
    [reservation] = world.reservations()
    assert reservation.source == ReservationSource.BOT
    assert reservation.status == ReservationStatus.CONFIRMED
    assert reservation.created_by_phone == OWNER and reservation.date == date(2026, 10, 9)
    [event] = world.session.scalars(
        select(BotEvent).where(BotEvent.event_type == "sum_reservation_created")
    )
    assert event.payload["reservation_id"] == reservation.id and event.phone_e164 == OWNER


@pytest.mark.parametrize("answer", ["No", "¿y el sábado?", "sí, pero a la tarde"])
def test_anything_but_a_yes_does_not_book(world: World, answer: str) -> None:
    _, asked = _book(world, OWNER)

    result, ctx = _confirm(world, OWNER, asked, answer)

    assert result["status"] == "confirmation_requested" and world.reservations() == []


def test_a_yes_to_another_summary_does_not_book(world: World) -> None:
    _, asked = _book(world, TWO_UNITS, unit_id=world.second_unit_id)

    # The model changes the unit when the person says yes: the summary is not the one shown.
    result, ctx = _confirm(world, TWO_UNITS, asked, "Sí, reservar", unit_id=world.third_unit_id)

    assert result["status"] == "confirmation_requested" and world.reservations() == []
    assert "• Unidad: COC.2" in ctx.offer.text  # type: ignore[union-attr]


def test_a_taken_or_missing_slot(world: World) -> None:
    _, asked = _book(world, OWNER)
    _confirm(world, OWNER, asked, "Sí, reservar")

    taken, ctx = _book(world, TENANT)
    assert taken["status"] == "not_possible" and taken["reasons"] == ["taken"]
    assert "ya está reservado" in taken["say"] and ctx.offer is None

    missing, _ = _book(world, OWNER, slot="18:00")
    assert missing["status"] == "no_such_slot"
    assert missing["slots_that_day"] == ["20:00 a 02:00 (del día siguiente) · Noche"]


def test_rules_of_the_service_apply(world: World) -> None:
    friday_10 = datetime(2026, 10, 9, 10, tzinfo=CBA)  # 10 h before: 24 h needed
    ctx = world.ctx(OWNER)
    ctx.now = friday_10

    result = run_tool(ctx, "book_sum", {"date": FRIDAY, "slot": "20:00"})

    assert result["status"] == "not_possible" and result["reasons"] == ["too_soon"]
    assert "24 h de anticipación" in result["say"]


# --- The person's reservations and cancelling ------------------------------------------------


def _reservation(world: World, unit_id: int, day: str = FRIDAY) -> Reservation:
    reservation = Reservation(
        amenity_id=world.amenity.id,
        slot_id=world.night.id,
        date=date.fromisoformat(day),
        unit_id=unit_id,
        source=ReservationSource.PANEL,
    )
    world.session.add(reservation)
    world.session.commit()
    return reservation


def test_my_reservations_lists_only_the_own_ones(world: World) -> None:
    mine = _reservation(world, world.unit_id)
    _reservation(world, world.second_unit_id, "2026-10-16")
    ctx = world.ctx(OWNER)

    result = run_tool(ctx, "my_sum_reservations", {})

    [item] = result["reservations"]
    assert item["reservation_id"] == mine.id and item["unit"] == "04-C"
    assert item["can_cancel"] is True and item["day"] == "viernes 09/10/2026"
    assert run_tool(world.ctx(UNKNOWN), "my_sum_reservations", {})["reason"] == "not_verified"


def test_cancel_asks_first_and_then_cancels(world: World) -> None:
    mine = _reservation(world, world.unit_id)
    ctx = world.ctx(OWNER)
    run_tool(ctx, "my_sum_reservations", {})

    asked = run_tool(ctx, "cancel_sum_reservation", {"reservation_id": mine.id})

    assert asked["status"] == "confirmation_requested"
    assert ctx.offer is not None and ctx.offer.titles == ["Sí, cancelar", "No"]
    assert world.reservations()[0].status == ReservationStatus.CONFIRMED

    shown = with_options(ctx.offer.text, ctx.offer.titles)
    second = world.ctx(OWNER, user_text="Sí, cancelar", last_bot_text=shown)
    run_tool(second, "my_sum_reservations", {})
    done = run_tool(second, "cancel_sum_reservation", {"reservation_id": mine.id})

    assert done["status"] == "cancelled"
    assert world.reservations()[0].status == ReservationStatus.CANCELLED
    [event] = world.session.scalars(
        select(BotEvent).where(BotEvent.event_type == "sum_reservation_cancelled")
    )
    assert event.payload["reservation_id"] == mine.id


def test_cancel_too_late_or_not_from_this_message(world: World) -> None:
    mine = _reservation(world, world.unit_id)
    late = world.ctx(OWNER)
    late.now = datetime(2026, 10, 8, 10, tzinfo=CBA)  # 34 h before: 48 h needed

    listed = run_tool(late, "my_sum_reservations", {})
    assert listed["reservations"][0]["can_cancel"] is False
    result = run_tool(late, "cancel_sum_reservation", {"reservation_id": mine.id})
    assert result["status"] == "not_possible" and "48 h" in result["say"]
    assert late.offer is None

    fresh = world.ctx(OWNER)  # my_sum_reservations not called in this message
    assert (
        run_tool(fresh, "cancel_sum_reservation", {"reservation_id": mine.id})["reason"]
        == "reservation_not_confirmed"
    )


def test_cannot_cancel_someone_elses(world: World) -> None:
    theirs = _reservation(world, world.second_unit_id)
    ctx = world.ctx(OWNER)
    run_tool(ctx, "my_sum_reservations", {})
    ctx.offered_reservation_ids.add(theirs.id)  # even if the id slipped in

    assert run_tool(ctx, "cancel_sum_reservation", {"reservation_id": theirs.id})["reason"] == (
        "not_yours"
    )


# --- Through the agent ---------------------------------------------------------------------


@pytest.mark.parametrize("name", PROVIDERS)
def test_agent_books_only_after_the_person_confirms(name: str, world: World) -> None:
    call = Call("book_sum", {"date": FRIDAY, "slot": "20:00"})
    provider, script = scripted_provider(name, [call, call, Say("¡Listo! Reservado.")])
    agent = Agent(provider, settings=Settings(_env_file=None), now=lambda: NOW)

    first = agent.reply(world.session, OWNER, "quiero el SUM el viernes a la noche")

    assert [c.title for c in first.choices] == ["Sí, reservar", "No"]
    assert "\n\nReserva del SUM de RODAS II:" in first.text  # after the greeting
    assert len(script.requests) == 1 and world.reservations() == []

    history = [m for m in first.history if isinstance(m, UserMessage | AssistantMessage)]
    history = [m for m in history if not getattr(m, "tool_calls", ())]
    second = agent.reply(world.session, OWNER, "Sí, reservar", history)

    assert "booked" in last_tool_result(name, script.requests[2])
    assert second.text == "¡Listo! Reservado."
    [reservation] = world.reservations()
    assert reservation.source == ReservationSource.BOT


def test_summary_as_the_first_message_starts_with_the_greeting(world: World) -> None:
    ctx = world.ctx(OWNER)
    ctx.first_message = True

    run_tool(ctx, "book_sum", {"date": FRIDAY, "slot": "20:00"})

    assert ctx.offer is not None
    assert ctx.offer.text.startswith(f"{ctx.greeting}\n\nReserva del SUM de RODAS II:")
    # The confirmation still matches: the summary is inside the previous message.
    booked, _ = _confirm(world, OWNER, ctx, "Sí, reservar")
    assert booked["status"] == "booked"
