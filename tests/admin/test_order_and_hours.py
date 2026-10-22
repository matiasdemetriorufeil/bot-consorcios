"""«Tipos de problema»: the general order with «Subir» / «Bajar» and «Guardar orden» (the server
recalculates 10, 20, 30… and refuses bad positions), no order field in the form, a new one last.
And the one hour picker of the panel (app.admin.hours): «Configuración de reclamos»,
«Configuración del bot» and the SUM's slots, with the stored value picked even off the list.
Invented data only."""

import re
from datetime import time
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin.hours import HALF_HOURS, accepted_hour, hour_choices
from app.db.models import Amenity, AmenitySlot, BotSettings, ClaimCategory, ClaimSettings
from tests.admin.conftest import ADMIN, Panel
from tests.bot import factories as f
from tests.claims import factories as cf

LIST = "/admin/claim-category/list"
ORDER = "/admin/claim-category/order"


def _categories(session: Session) -> dict[str, int]:
    session.expire_all()
    return {c.list_title: c.sort_order for c in session.scalars(select(ClaimCategory))}


@pytest.fixture
def three(db_session: Session) -> dict[str, int]:
    """Ascensor (10), Agua (20), Gas (30): their ids by title."""
    rows = {
        title: cf.category(
            db_session, f"Problema {title} inventado", list_title=title, sort_order=order
        )  # fmt: skip
        for title, order in (("Ascensor", 10), ("Agua", 20), ("Gas", 30))
    }
    db_session.commit()
    return {title: c.id for title, c in rows.items()}


# --- Tipos de problema: the order -----------------------------------------------------------


def test_the_list_shows_places_and_buttons(logged_in: Panel, three: dict[str, int]) -> None:
    page = logged_in.client.get(LIST).text
    assert 'id="category-order"' in page and "Guardar orden" in page
    for place, title in enumerate(("Ascensor", "Agua", "Gas"), start=1):
        assert f'name="position_{three[title]}" value="{place}"' in page
    assert 'aria-label="Subir Ascensor" disabled' in page
    assert 'aria-label="Bajar Gas" disabled' in page
    assert "data-reorder" in page and 'data-reorder-status="category-order-status"' in page
    # Never the internal number.
    assert not re.search(r">\s*(10|20|30)\s*<", page.split('id="category-order"', 1)[1])
    assert "En cada edificio se puede cambiar en «Reclamos por edificio»" in page


def test_save_the_order_recalculates_and_is_audited(
    logged_in: Panel, three: dict[str, int]
) -> None:
    data = {f"position_{three['Gas']}": "1", f"position_{three['Ascensor']}": "2",
            f"position_{three['Agua']}": "3"}  # fmt: skip
    response = logged_in.client.post(ORDER, data=data, follow_redirects=True)
    assert "Orden guardado." in response.text
    assert _categories(logged_in.session) == {"Gas": 10, "Ascensor": 20, "Agua": 30}
    [event] = logged_in.admin_events("claim_categories_reordered")
    assert event["admin_user"] == ADMIN and set(event["category_ids"]) == set(three.values())
    # The same again: nothing changes, no new event.
    logged_in.client.post(ORDER, data=data)
    assert len(logged_in.admin_events("claim_categories_reordered")) == 1


@pytest.mark.parametrize(
    "positions",
    [
        ("1", "1", "2"),  # repeated
        ("0", "1", "2"),  # from 1
        ("-1", "1", "2"),  # negative
        ("1.5", "1", "2"),  # not whole
        ("uno", "2", "3"),  # not a number
    ],
)
def test_bad_positions_are_refused(
    logged_in: Panel, three: dict[str, int], positions: tuple[str, str, str]
) -> None:
    data = {
        f"position_{three[t]}": p
        for t, p in zip(("Ascensor", "Agua", "Gas"), positions, strict=True)
    }
    response = logged_in.client.post(ORDER, data=data, follow_redirects=True)
    assert "El orden de la tabla llegó mal" in response.text
    assert _categories(logged_in.session) == {"Ascensor": 10, "Agua": 20, "Gas": 30}
    assert logged_in.admin_events("claim_categories_reordered") == []


def test_one_not_in_the_form_keeps_its_place_after(logged_in: Panel, three: dict[str, int]) -> None:
    """A kind added while the list was open: after the ones that were saved."""
    data = {f"position_{three['Agua']}": "1", f"position_{three['Ascensor']}": "2"}
    logged_in.client.post(ORDER, data=data)
    assert _categories(logged_in.session) == {"Agua": 10, "Ascensor": 20, "Gas": 30}


@pytest.mark.parametrize("query", ["?search=gas", "?active=true", "?sortBy=name&sort=asc"])
def test_no_buttons_on_part_of_the_list(
    logged_in: Panel, three: dict[str, int], query: str
) -> None:
    page = logged_in.client.get(LIST + query).text
    assert "Guardar orden" not in page and "data-move-buttons" not in page
    assert "Para cambiar el orden, borrá la búsqueda y los filtros." in page


def test_a_new_kind_goes_last_and_the_order_is_never_typed(
    logged_in: Panel, three: dict[str, int]
) -> None:
    form = logged_in.client.get("/admin/claim-category/create").text
    assert 'name="sort_order"' not in form
    data = {
        "name": "Problema Plagas inventado",
        "list_title": "Plagas",
        "scope": "unit",
        "active": "y",
        "save": "Save",
        "sort_order": "-5",
    }  # fmt: skip (sent by hand)
    response = logged_in.client.post(
        "/admin/claim-category/create", data=data, follow_redirects=False
    )
    assert response.status_code == 302
    assert _categories(logged_in.session)["Plagas"] == 40
    # Editing by hand cannot change it either.
    plagas = logged_in.session.scalar(
        select(ClaimCategory).where(ClaimCategory.list_title == "Plagas")
    )
    assert plagas is not None
    edit = {**data, "sort_order": "0", "list_title": "Plagas y bichos"}
    logged_in.client.post(f"/admin/claim-category/edit/{plagas.id}", data=edit)
    assert _categories(logged_in.session)["Plagas y bichos"] == 40


def test_the_first_kind_starts_at_ten(logged_in: Panel) -> None:
    data = {"name": "Problema inventado", "list_title": "Primero", "scope": "unit",
            "active": "y", "save": "Save"}  # fmt: skip
    logged_in.client.post("/admin/claim-category/create", data=data)
    assert _categories(logged_in.session) == {"Primero": 10}


def test_only_admins_save_the_order(operator: Panel, three: dict[str, int]) -> None:
    data = {f"position_{three['Gas']}": "1"}
    assert operator.client.post(ORDER, data=data).status_code == 403
    assert _categories(operator.session) == {"Ascensor": 10, "Agua": 20, "Gas": 30}


# --- The hour picker -------------------------------------------------------------------------


def test_the_list_of_hours() -> None:
    assert HALF_HOURS[0] == "00:00" and HALF_HOURS[-1] == "23:30" and len(HALF_HOURS) == 48
    assert [v for v, _ in hour_choices()] == HALF_HOURS
    # A stored value off the list stays, in its place.
    values = [v for v, _ in hour_choices("20:15")]
    assert values.index("20:15") == values.index("20:00") + 1
    assert hour_choices(empty="Por defecto")[0] == ("", "Por defecto")
    assert accepted_hour("8:00") == "08:00" and accepted_hour("07:13") is None
    assert accepted_hour("07:13", stored="07:13") == "07:13"


def _selected(page: str, name: str) -> str | None:
    select_html = page.split(f'name="{name}"', 1)[1].split("</select>", 1)[0]
    found = re.search(
        r'<option[^>]*selected[^>]*value="([^"]*)"|<option value="([^"]*)" selected', select_html
    )
    return next((g for g in found.groups() if g is not None), None) if found else None


def _claim_form(**values: Any) -> dict[str, Any]:
    return {"provider_weekdays": ["0", "1", "2", "3", "4"], "provider_hours_start": "09:00",
            "provider_hours_end": "18:30", "urgent_any_time": "y", "reminder_hours": "2",
            "reminder_urgent_minutes": "20", "alert_hours": "6", "alert_urgent_minutes": "45",
            "stale_days": "5", "save": "Save", **values}  # fmt: skip


def test_claim_settings_hours_are_lists_with_the_value_picked(logged_in: Panel) -> None:
    page = logged_in.client.get("/admin/claim-settings/edit/1").text
    assert "<select" in page.split('name="provider_hours_start"', 1)[0].rsplit("<", 1)[1] or (
        re.search(r'<select[^>]*name="provider_hours_start"', page)
    )
    assert re.search(r'<select[^>]*class="form-select"[^>]*name="provider_hours_start"', page)
    assert _selected(page, "provider_hours_start") == "08:00"
    assert _selected(page, "provider_hours_end") == "20:00"
    assert 'type="time"' not in page and "HH:MM" not in page


def test_an_hour_off_the_list_is_kept(logged_in: Panel, db_session: Session) -> None:
    row = db_session.get(ClaimSettings, 1)
    assert row is not None
    row.provider_hours_end = "20:15"
    db_session.commit()
    page = logged_in.client.get("/admin/claim-settings/edit/1").text
    assert _selected(page, "provider_hours_end") == "20:15"
    # Saved unchanged: accepted and kept.
    response = logged_in.client.post(
        "/admin/claim-settings/edit/1",
        data=_claim_form(provider_hours_end="20:15"),
        follow_redirects=False,
    )
    assert response.status_code == 302
    db_session.expire_all()
    assert db_session.get(ClaimSettings, 1).provider_hours_end == "20:15"


def test_hasta_before_desde_is_refused(logged_in: Panel, db_session: Session) -> None:
    response = logged_in.client.post(
        "/admin/claim-settings/edit/1",
        data=_claim_form(provider_hours_start="18:00", provider_hours_end="09:00"),
    )
    assert response.status_code == 400
    assert "El horario «hasta» tiene que ser después del «desde»." in response.text


def _bot_form(**values: str) -> dict[str, str]:
    return {"welcome_message": "", "office_hours_start": "", "office_hours_end": "",
            "office_weekdays": "", "out_of_hours_text": "", "emergency_contact_text": "",
            "autogestion_url": "", "payment_code_how_to": "", "save": "Save", **values}  # fmt: skip


def test_bot_office_hours_are_lists_with_a_default(logged_in: Panel, db_session: Session) -> None:
    page = logged_in.client.get("/admin/bot-settings/edit/1").text
    assert re.search(r'<select[^>]*name="office_hours_start"', page)
    assert '<option value="">Por defecto</option>' in page or '<option selected value="">' in page
    response = logged_in.client.post(
        "/admin/bot-settings/edit/1",
        data=_bot_form(office_hours_start="09:30", office_hours_end="17:00"),
        follow_redirects=False,
    )
    assert response.status_code == 302
    db_session.expire_all()
    row = db_session.get(BotSettings, 1)
    assert row is not None and (row.office_hours_start, row.office_hours_end) == ("09:30", "17:00")
    page = logged_in.client.get("/admin/bot-settings/edit/1").text
    assert _selected(page, "office_hours_start") == "09:30"


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        ("17:00", "09:00", "«hasta» tiene que ser después del «desde»"),
        ("09:00", "09:00", "«hasta» tiene que ser después del «desde»"),
        ("09:13", "17:00", "Elegí una hora de la lista."),
    ],
)
def test_bot_office_hours_are_checked(
    logged_in: Panel, db_session: Session, start: str, end: str, message: str
) -> None:
    response = logged_in.client.post(
        "/admin/bot-settings/edit/1", data=_bot_form(office_hours_start=start, office_hours_end=end)
    )
    assert response.status_code == 400 and message in response.text
    db_session.expire_all()
    assert db_session.get(BotSettings, 1).office_hours_start is None


@pytest.fixture
def amenity(db_session: Session) -> int:
    building = f.building(db_session, "092 TORRE DE HORAS")
    sum_ = Amenity(building_id=building.id, max_advance_days=30)
    sum_.slots = [AmenitySlot(weekday=4, start_time=time(20), end_time=time(23, 59))]
    db_session.add(sum_)
    db_session.commit()
    return sum_.id


def test_sum_slots_pick_hours_from_the_list(
    logged_in: Panel, amenity: int, db_session: Session
) -> None:
    page = logged_in.client.get(f"/admin/amenities/{amenity}/config").text
    assert 'type="time"' not in page
    assert re.search(r'<select class="form-select" id="start" name="start" required>', page)
    assert '<option value="23:30">23:30</option>' in page
    assert "20:00 a 23:59" in page  # a stored slot off the list is still shown
    url = f"/admin/amenities/{amenity}/slots"
    logged_in.client.post(url, data={"weekday": "5", "start": "20:15", "end": "23:00"})
    assert logged_in.admin_events("amenity_slots_added") == []  # off the list: refused
    logged_in.client.post(url, data={"weekday": "5", "start": "20:00", "end": "00:00"})
    [event] = logged_in.admin_events("amenity_slots_added")
    db_session.expire_all()
    slot = db_session.get(AmenitySlot, event["slot_ids"][0])
    assert slot is not None and (slot.start_time, slot.end_time) == (time(20), time(0))
