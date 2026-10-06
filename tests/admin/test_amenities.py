"""Panel pages of the SUM ("Reservas de SUM") and the "Reclamos" placeholder, against the
Postgres test database. Invented data only."""

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin.amenities import AmenitiesView
from app.db.models import (
    Amenity,
    AmenitySlot,
    Reservation,
    ReservationSource,
    ReservationStatus,
)
from tests.admin.conftest import ADMIN, Panel
from tests.bot import factories as f

CBA = ZoneInfo("America/Argentina/Cordoba")
NOW = datetime(2026, 10, 5, 10, 0, tzinfo=CBA)  # Monday
FRIDAY = date(2026, 10, 9)


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AmenitiesView, "now", lambda self: NOW)


@dataclass
class Sum:
    amenity_id: int
    building_id: int
    afternoon_id: int  # Fridays 14 to 18
    night_id: int  # Fridays 20 to 02
    unit_id: int
    other_unit_id: int


@pytest.fixture
def sum_(db_session: Session) -> Sum:
    building = f.building(db_session, "091 TORRE DEL PANEL")
    amenity = Amenity(building_id=building.id, max_advance_days=30)
    afternoon = AmenitySlot(weekday=4, start_time=time(14), end_time=time(18))
    night = AmenitySlot(weekday=4, start_time=time(20), end_time=time(2), label="Noche")
    amenity.slots = [afternoon, night]
    db_session.add(amenity)
    unit = f.unit(db_session, building, "01-A")
    other = f.unit(db_session, building, "02-B")
    db_session.commit()
    return Sum(amenity.id, building.id, afternoon.id, night.id, unit.id, other.id)


def _reservations(session: Session, amenity_id: int) -> list[Reservation]:
    session.expire_all()
    return list(
        session.scalars(
            select(Reservation).where(Reservation.amenity_id == amenity_id).order_by(Reservation.id)
        )
    )


def _post(panel: Panel, url: str, data: dict[str, Any]) -> Any:
    return panel.client.post(url, data=data, follow_redirects=False)


# --- Menu, login and the claims page -----------------------------------------------------


def test_pages_need_login(panel: Panel, sum_: Sum) -> None:
    for url in ("/admin/amenities", f"/admin/amenities/{sum_.amenity_id}/week", "/admin/claims"):
        response = panel.client.get(url, follow_redirects=False)
        assert response.status_code == 302 and "/admin/login" in response.headers["location"]


def test_menu_has_reservations_and_then_claims(logged_in: Panel) -> None:
    page = logged_in.client.get("/admin/").text
    assert page.index("Reservas de SUM") < page.index("Reclamos")

    claims = logged_in.client.get("/admin/claims")
    assert claims.status_code == 200
    assert "Próximamente: acá vas a ver y seguir los reclamos de los propietarios." in claims.text


# --- List and creation ---------------------------------------------------------------------


def test_list_shows_each_sum_and_adds_one(logged_in: Panel, sum_: Sum, db_session: Session) -> None:
    other = f.building(db_session, "092 EDIFICIO SIN SUM")
    db_session.commit()
    _post(
        logged_in,
        "/admin/amenities/new",
        {"building_id": other.id},
    )
    page = logged_in.client.get("/admin/amenities").text

    assert "TORRE DEL PANEL" in page and "EDIFICIO SIN SUM" in page
    assert "2 turno(s) por semana" in page and "el bot no reserva" in page
    created = db_session.scalar(select(Amenity).where(Amenity.building_id == other.id))
    assert created is not None and created.name == "SUM" and not created.bot_booking_enabled
    [event] = logged_in.admin_events("amenity_created")
    assert event["admin_user"] == ADMIN
    assert event["amenity_id"] == created.id and event["building_id"] == other.id


def test_a_building_gets_one_sum_only(logged_in: Panel, sum_: Sum, db_session: Session) -> None:
    response = _post(logged_in, "/admin/amenities/new", {"building_id": sum_.building_id})

    assert response.status_code == 302
    assert len(db_session.scalars(select(Amenity)).all()) == 1
    assert logged_in.admin_events("amenity_created") == []


# --- Configuration -------------------------------------------------------------------------


def _config(**changes: Any) -> dict[str, Any]:
    form = {
        "name": "SUM",
        "rules_text": "Dejar limpio. Música hasta las 2.",
        "min_advance_hours": "24",
        "max_advance_days": "30",
        "max_per_unit_per_month": "",
        "cancel_until_hours": "48",
        "active": "on",
        "bot_booking_enabled": "on",
    }
    return form | changes


def test_config_saves_and_audits_changed_fields(
    logged_in: Panel, sum_: Sum, db_session: Session
) -> None:
    url = f"/admin/amenities/{sum_.amenity_id}/config"
    assert logged_in.client.get(url).status_code == 200

    assert _post(logged_in, url, _config()).status_code == 302

    db_session.expire_all()
    amenity = db_session.get(Amenity, sum_.amenity_id)
    assert amenity.bot_booking_enabled and not amenity.blocks_debtors
    assert amenity.max_per_unit_per_month is None  # blank = no limit
    assert amenity.rules_text == "Dejar limpio. Música hasta las 2."
    [event] = logged_in.admin_events("amenity_updated")
    assert event["fields"] == ["bot_booking_enabled", "max_per_unit_per_month", "rules_text"]
    assert "Música" not in str(event)  # never the texts


def test_config_rejects_bad_numbers(logged_in: Panel, sum_: Sum, db_session: Session) -> None:
    url = f"/admin/amenities/{sum_.amenity_id}/config"

    response = _post(logged_in, url, _config(max_advance_days="0"))

    assert response.status_code == 400 and "Anticipación máxima" in response.text
    db_session.expire_all()
    assert db_session.get(Amenity, sum_.amenity_id).max_advance_days == 30
    assert logged_in.admin_events("amenity_updated") == []


# --- Slots ---------------------------------------------------------------------------------


def _slots(session: Session, amenity_id: int) -> list[tuple[int, time, time, bool]]:
    session.expire_all()
    return [
        (s.weekday, s.start_time, s.end_time, s.active)
        for s in session.scalars(
            select(AmenitySlot)
            .where(AmenitySlot.amenity_id == amenity_id)
            .order_by(AmenitySlot.weekday, AmenitySlot.start_time)
        )
    ]


def test_add_a_slot_to_several_days_and_copy_a_day(
    logged_in: Panel, sum_: Sum, db_session: Session
) -> None:
    url = f"/admin/amenities/{sum_.amenity_id}/slots"
    # Saturday and Sunday, 21 to 3 (ends the next day).
    _post(logged_in, url, {"weekday": ["5", "6"], "start": "21:00", "end": "03:00", "label": ""})
    # Friday onto Monday and Friday itself (Friday is skipped: same day).
    _post(logged_in, url, {"copy_from": "4", "to": ["0", "4"]})
    # Again: nothing new.
    _post(logged_in, url, {"copy_from": "4", "to": ["0"]})

    assert _slots(db_session, sum_.amenity_id) == [
        (0, time(14), time(18), True),
        (0, time(20), time(2), True),
        (4, time(14), time(18), True),
        (4, time(20), time(2), True),
        (5, time(21), time(3), True),
        (6, time(21), time(3), True),
    ]
    added, copied = logged_in.admin_events("amenity_slots_added") + logged_in.admin_events(
        "amenity_slots_copied"
    )
    assert len(added["slot_ids"]) == 2
    assert copied["from_weekday"] == 4 and copied["to_weekdays"] == [0]
    assert len(logged_in.admin_events("amenity_slots_copied")) == 1  # the repeat added nothing
    page = logged_in.client.get(f"/admin/amenities/{sum_.amenity_id}/config").text
    assert "21:00 a 03:00 (del día siguiente)" in page


def test_add_slot_needs_days_and_different_times(logged_in: Panel, sum_: Sum) -> None:
    url = f"/admin/amenities/{sum_.amenity_id}/slots"
    _post(logged_in, url, {"start": "10:00", "end": "12:00"})
    _post(logged_in, url, {"weekday": "1", "start": "10:00", "end": "10:00"})
    assert logged_in.admin_events("amenity_slots_added") == []


def test_remove_a_slot(logged_in: Panel, sum_: Sum, db_session: Session) -> None:
    base = f"/admin/amenities/{sum_.amenity_id}/slots"
    # A past reservation of the night slot: it stays, inactive.
    db_session.add(
        Reservation(
            amenity_id=sum_.amenity_id,
            slot_id=sum_.night_id,
            date=date(2026, 10, 2),
            unit_id=sum_.unit_id,
            source=ReservationSource.PANEL,
        )
    )
    db_session.commit()

    _post(logged_in, f"{base}/{sum_.afternoon_id}/remove", {})
    _post(logged_in, f"{base}/{sum_.night_id}/remove", {})

    assert _slots(db_session, sum_.amenity_id) == [(4, time(20), time(2), False)]
    events = logged_in.admin_events("amenity_slot_removed")
    assert [e["kept_inactive"] for e in events] == [False, True]


def test_a_slot_with_future_reservations_is_not_removed(
    logged_in: Panel, sum_: Sum, db_session: Session
) -> None:
    _book(logged_in, sum_, sum_.afternoon_id, FRIDAY, sum_.unit_id)

    _post(logged_in, f"/admin/amenities/{sum_.amenity_id}/slots/{sum_.afternoon_id}/remove", {})

    assert (4, time(14), time(18), True) in _slots(db_session, sum_.amenity_id)
    assert logged_in.admin_events("amenity_slot_removed") == []


# --- Weekly grid ----------------------------------------------------------------------------


def _book(panel: Panel, sum_: Sum, slot_id: int, day: date, unit_id: int, **extra: Any) -> Any:
    return _post(
        panel,
        f"/admin/amenities/{sum_.amenity_id}/book",
        {"slot": slot_id, "date": day.isoformat(), "unit_id": unit_id, **extra},
    )


def test_week_grid_shows_free_and_taken_slots(logged_in: Panel, sum_: Sum) -> None:
    _book(logged_in, sum_, sum_.night_id, FRIDAY, sum_.other_unit_id)

    page = logged_in.client.get(f"/admin/amenities/{sum_.amenity_id}/week").text

    assert "Lu 05/10" in page and "Do 11/10" in page  # Monday to Sunday of this week
    assert "14:00 a 18:00" in page and "termina al día siguiente" in page
    assert page.count("slot-taken") == 2  # grid and phone list
    assert "02-B" in page
    assert f"slot={sum_.afternoon_id}&date=2026-10-09" in page  # free: links to book
    assert "?start=2026-09-28" in page and "?start=2026-10-12" in page


def test_week_navigation_and_past_slots(logged_in: Panel, sum_: Sum) -> None:
    previous = logged_in.client.get(f"/admin/amenities/{sum_.amenity_id}/week?start=2026-10-01")
    assert "Lu 28/09" in previous.text
    assert "slot-free" not in previous.text and "Pasado" in previous.text  # 02/10 is past

    following = logged_in.client.get(f"/admin/amenities/{sum_.amenity_id}/week?start=2026-10-14")
    assert "Lu 12/10" in following.text and "Vi 16/10" in following.text


# --- Booking from the panel -----------------------------------------------------------------


def test_book_a_free_slot(logged_in: Panel, sum_: Sum, db_session: Session) -> None:
    form = logged_in.client.get(
        f"/admin/amenities/{sum_.amenity_id}/book?slot={sum_.afternoon_id}&date=2026-10-09"
    )
    assert form.status_code == 200 and "01-A" in form.text and "02-B" in form.text

    response = _book(logged_in, sum_, sum_.afternoon_id, FRIDAY, sum_.unit_id, notes="Cumple")

    assert response.status_code == 302
    assert "start=2026-10-09" in response.headers["location"]
    [reservation] = _reservations(db_session, sum_.amenity_id)
    assert reservation.source == ReservationSource.PANEL
    assert reservation.status == ReservationStatus.CONFIRMED and reservation.notes == "Cumple"
    [event] = logged_in.admin_events("reservation_created")
    assert event["reservation_id"] == reservation.id and event["unit_id"] == sum_.unit_id
    assert event["date"] == "2026-10-09" and event["overridden"] == []


def test_a_taken_slot_cannot_be_booked_again(
    logged_in: Panel, sum_: Sum, db_session: Session
) -> None:
    _book(logged_in, sum_, sum_.afternoon_id, FRIDAY, sum_.unit_id)

    response = _book(logged_in, sum_, sum_.afternoon_id, FRIDAY, sum_.other_unit_id, override="yes")

    assert response.status_code == 400 and "ya está reservado" in response.text
    assert "Reservar igual" not in response.text  # not a policy rule
    assert len(_reservations(db_session, sum_.amenity_id)) == 1


def test_policy_rules_can_be_skipped_with_a_record(
    logged_in: Panel, sum_: Sum, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    friday_9 = datetime(2026, 10, 9, 9, tzinfo=CBA)  # 5 h before the slot: too soon
    monkeypatch.setattr(AmenitiesView, "now", lambda self: friday_9)

    refused = _book(logged_in, sum_, sum_.afternoon_id, FRIDAY, sum_.unit_id)
    assert refused.status_code == 400
    assert "24 h de anticipación" in refused.text and "Reservar igual" in refused.text
    assert _reservations(db_session, sum_.amenity_id) == []

    accepted = _book(logged_in, sum_, sum_.afternoon_id, FRIDAY, sum_.unit_id, override="yes")

    assert accepted.status_code == 302
    assert len(_reservations(db_session, sum_.amenity_id)) == 1
    [event] = logged_in.admin_events("reservation_created")
    assert event["overridden"] == ["too_soon"]


def test_book_rejects_a_unit_of_another_building(
    logged_in: Panel, sum_: Sum, db_session: Session
) -> None:
    stranger = f.unit(db_session, f.building(db_session, "093 AJENO"), "09-Z")
    db_session.commit()

    response = _book(logged_in, sum_, sum_.afternoon_id, FRIDAY, stranger.id, override="yes")

    assert response.status_code == 400 and "no es de este edificio" in response.text
    assert _reservations(db_session, sum_.amenity_id) == []


# --- Detail and cancellation ----------------------------------------------------------------


def test_cancel_needs_confirmation_and_frees_the_slot(
    logged_in: Panel, sum_: Sum, db_session: Session
) -> None:
    _book(logged_in, sum_, sum_.night_id, FRIDAY, sum_.unit_id)
    [reservation] = _reservations(db_session, sum_.amenity_id)
    url = f"/admin/amenities/{sum_.amenity_id}/reservations/{reservation.id}"

    detail = logged_in.client.get(url)
    assert detail.status_code == 200
    assert "01-A" in detail.text and "20:00 a 02:00 (del día siguiente) · Noche" in detail.text
    assert "Cancelar reserva" in detail.text

    assert _post(logged_in, url, {}).status_code == 400  # no confirmation
    assert _reservations(db_session, sum_.amenity_id)[0].status == ReservationStatus.CONFIRMED

    assert _post(logged_in, url, {"confirm": "yes"}).status_code == 302

    [cancelled] = _reservations(db_session, sum_.amenity_id)
    assert cancelled.status == ReservationStatus.CANCELLED and cancelled.cancelled_at == NOW
    [event] = logged_in.admin_events("reservation_cancelled")
    assert event["reservation_id"] == reservation.id
    # The slot is free again.
    week = logged_in.client.get(f"/admin/amenities/{sum_.amenity_id}/week").text
    assert "slot-taken" not in week
    assert "Cancelar reserva" not in logged_in.client.get(url).text


def test_the_panel_cancels_even_close_to_the_start(
    logged_in: Panel, sum_: Sum, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _book(logged_in, sum_, sum_.night_id, FRIDAY, sum_.unit_id)
    [reservation] = _reservations(db_session, sum_.amenity_id)
    monkeypatch.setattr(AmenitiesView, "now", lambda self: datetime(2026, 10, 9, 19, tzinfo=CBA))

    url = f"/admin/amenities/{sum_.amenity_id}/reservations/{reservation.id}"
    assert _post(logged_in, url, {"confirm": "yes"}).status_code == 302
    assert _reservations(db_session, sum_.amenity_id)[0].status == ReservationStatus.CANCELLED


def test_unknown_ids_are_404(logged_in: Panel, sum_: Sum) -> None:
    for url in (
        "/admin/amenities/999999/week",
        "/admin/amenities/999999/config",
        f"/admin/amenities/{sum_.amenity_id}/reservations/999999",
        f"/admin/amenities/{sum_.amenity_id}/book?slot=999999&date=2026-10-09",
    ):
        assert logged_in.client.get(url).status_code == 404, url
