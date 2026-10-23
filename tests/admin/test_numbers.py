"""The panel's numbers of settings (app.admin.numbers): picked from a list with a text each in
«Configuración de reclamos» and the SUM's set-up, a whole number in bounds for the order of
«Respuestas rápidas». The current value is picked; a value off the list sent by hand is
refused on the server, with the message in Spanish next to the field; a value already stored
off the list is shown and kept when saved unchanged. And the browser never validates by itself:
every form of the panel has novalidate, every message comes from the server. Invented data."""

import re
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin.numbers import (
    CLAIM_DAYS,
    CLAIM_HOURS,
    CLAIM_MINUTES,
    NOT_IN_LIST,
    SUM_CANCEL_HOURS,
    SUM_MIN_ADVANCE_HOURS,
    SUM_PER_UNIT_MONTH,
    Bounds,
    check_choice,
    check_number,
)
from app.db.models import Amenity, ClaimSettings, QuickReply
from tests.admin.conftest import Panel
from tests.bot import factories as f

ADMIN_DIR = Path(__file__).resolve().parents[2] / "app" / "admin"


def test_the_texts_of_the_options() -> None:
    assert (CLAIM_HOURS.label(1), CLAIM_HOURS.label(4)) == ("1 hora", "4 horas")
    assert CLAIM_MINUTES.label(30) == "30 minutos"
    assert (CLAIM_DAYS.label(1), CLAIM_DAYS.label(14)) == ("1 día", "14 días")
    assert SUM_MIN_ADVANCE_HOURS.label(0) == "Sin mínimo"
    assert SUM_CANCEL_HOURS.label(0) == "Hasta que empieza"
    assert SUM_PER_UNIT_MONTH.choices()[0] == ("", "Sin límite")
    assert [v for v, _ in CLAIM_DAYS.choices()] == [str(d) for d in (*range(1, 8), 10, 14, 21, 30)]
    # A stored value off the list, in its place.
    values = [v for v, _ in CLAIM_DAYS.choices(45)]
    assert values[-1] == "45" and ("45", "45 días") in CLAIM_DAYS.choices(45)


def test_check_choice() -> None:
    assert check_choice("4", CLAIM_HOURS) == 4
    for bad in ("7", "-1", "0", "4.5", "", "cuatro"):
        with pytest.raises(ValueError, match=NOT_IN_LIST):
            check_choice(bad, CLAIM_HOURS)
    assert check_choice("7", CLAIM_HOURS, stored=7) == 7  # stored: kept
    assert check_choice("", SUM_PER_UNIT_MONTH) is None  # «Sin límite»


def test_check_number() -> None:
    bounds = Bounds(0, 999)
    for bad in ("-1", "1.5", "", "1000", "diez"):
        with pytest.raises(ValueError, match="número entero de 0 a 999"):
            check_number(bad, bounds)
    assert (check_number("0", bounds), check_number("999", bounds)) == (0, 999)


def _selected(page: str, name: str) -> tuple[str, str] | None:
    """(value, text) of the option picked in the <select name=...>."""
    found = re.search(rf'<select[^>]*name="{name}"[^>]*>(.*?)</select>', page, re.S)
    assert found, f"no <select> {name}"
    for option in re.findall(r"<option\b([^>]*)>([^<]*)<", found.group(1)):
        attrs, text = option
        value = re.search(r'value="([^"]*)"', attrs)
        if "selected" in attrs and value:
            return value.group(1), text.strip()
    return None


def _field_error(page: str, name: str) -> str:
    found = re.search(rf'id="{name}-error">\s*(?:<div>)?([^<]+)<', page)
    return found.group(1).strip() if found else ""


# --- Configuración de reclamos -------------------------------------------------------------

CLAIM_FORM: dict[str, Any] = {
    "provider_weekdays": ["0", "1", "2", "3", "4", "5"], "provider_hours_start": "08:00",
    "provider_hours_end": "20:00", "urgent_any_time": "y", "reminder_hours": "4",
    "reminder_urgent_minutes": "30", "alert_hours": "8", "alert_urgent_minutes": "60",
    "stale_days": "3", "save": "Save",
}  # fmt: skip
CLAIM_FIELDS = ["reminder_hours", "reminder_urgent_minutes", "alert_hours",
                "alert_urgent_minutes", "stale_days"]  # fmt: skip


def _claim_post(panel: Panel, **change: str) -> Any:
    return panel.client.post(
        "/admin/claim-settings/edit/1", data={**CLAIM_FORM, **change}, follow_redirects=False
    )


def test_claim_numbers_are_lists_with_the_value_picked(logged_in: Panel) -> None:
    page = logged_in.client.get("/admin/claim-settings/edit/1").text
    assert _selected(page, "reminder_hours") == ("4", "4 horas")
    assert _selected(page, "reminder_urgent_minutes") == ("30", "30 minutos")
    assert _selected(page, "alert_hours") == ("8", "8 horas")
    assert _selected(page, "alert_urgent_minutes") == ("60", "60 minutos")
    assert _selected(page, "stale_days") == ("3", "3 días")
    assert 'type="number"' not in page


@pytest.mark.parametrize("name", CLAIM_FIELDS)
@pytest.mark.parametrize("bad", ["9", "-1", "0", "1.5", ""])  # 9: in no list
def test_claim_numbers_refuse_values_off_the_list(
    logged_in: Panel, db_session: Session, name: str, bad: str
) -> None:
    response = _claim_post(logged_in, **{name: bad})
    assert response.status_code == 400
    assert _field_error(response.text, name) == NOT_IN_LIST
    db_session.expire_all()
    row = db_session.get(ClaimSettings, 1)
    assert row is not None and (row.reminder_hours, row.stale_days) == (4, 3)


def test_claim_numbers_accept_the_list_and_keep_the_old_rules(
    logged_in: Panel, db_session: Session
) -> None:
    edges = {"reminder_hours": "1", "alert_hours": "72", "reminder_urgent_minutes": "5",
             "alert_urgent_minutes": "240", "stale_days": "30"}  # fmt: skip
    assert _claim_post(logged_in, **edges).status_code == 302
    db_session.expire_all()
    row = db_session.get(ClaimSettings, 1)
    assert row is not None and (row.reminder_hours, row.alert_hours, row.stale_days) == (1, 72, 30)
    response = _claim_post(logged_in, reminder_hours="8", alert_hours="8")
    assert response.status_code == 400 and "después del recordatorio" in response.text


def test_a_stored_value_off_the_list_is_kept(logged_in: Panel, db_session: Session) -> None:
    row = db_session.get(ClaimSettings, 1)
    assert row is not None
    row.stale_days = 45  # loaded before the list
    db_session.commit()
    page = logged_in.client.get("/admin/claim-settings/edit/1").text
    assert _selected(page, "stale_days") == ("45", "45 días")
    assert _claim_post(logged_in, stale_days="45").status_code == 302  # unchanged: kept
    db_session.expire_all()
    assert db_session.get(ClaimSettings, 1).stale_days == 45
    response = _claim_post(logged_in, stale_days="46")  # another one off the list
    assert response.status_code == 400 and _field_error(response.text, "stale_days")


# --- The SUM's set-up ------------------------------------------------------------------------


@pytest.fixture
def amenity(db_session: Session) -> int:
    building = f.building(db_session, "096 TORRE NUMEROS")
    sum_ = Amenity(building_id=building.id)
    db_session.add(sum_)
    db_session.commit()
    return sum_.id


SUM_FORM = {"name": "SUM", "rules_text": "", "min_advance_hours": "24", "max_advance_days": "60",
            "max_per_unit_per_month": "2", "cancel_until_hours": "48", "active": "on"}  # fmt: skip
SUM_BAD = {
    "min_advance_hours": ["5", "-1", "1.5", "", "169"],
    "max_advance_days": ["4", "0", "-1", "", "366"],
    "max_per_unit_per_month": ["11", "0", "-1", "1.5"],
    "cancel_until_hours": ["5", "-1", "1.5", ""],
}


def _sum_post(panel: Panel, amenity: int, **change: str) -> Any:
    return panel.client.post(
        f"/admin/amenities/{amenity}/config", data={**SUM_FORM, **change}, follow_redirects=False
    )


def test_sum_numbers_are_lists_with_their_texts(logged_in: Panel, amenity: int) -> None:
    page = logged_in.client.get(f"/admin/amenities/{amenity}/config").text
    assert _selected(page, "min_advance_hours") == ("24", "24 horas")
    assert _selected(page, "max_advance_days") == ("60", "60 días")
    assert _selected(page, "max_per_unit_per_month") == ("2", "2 reservas")
    assert _selected(page, "cancel_until_hours") == ("48", "48 horas")
    assert '<option value="0">Sin mínimo</option>' in page
    assert '<option value="0">Hasta que empieza</option>' in page
    assert '<option value="">Sin límite</option>' in page
    assert 'type="number"' not in page


@pytest.mark.parametrize(("name", "bad"), [(n, b) for n, bads in SUM_BAD.items() for b in bads])
def test_sum_numbers_refuse_values_off_the_list(
    logged_in: Panel, db_session: Session, amenity: int, name: str, bad: str
) -> None:
    response = _sum_post(logged_in, amenity, **{name: bad})
    assert response.status_code == 400
    assert _field_error(response.text, name) == NOT_IN_LIST
    assert re.search(rf'<select[^>]*name="{name}"[^>]*aria-invalid="true"', response.text)
    db_session.expire_all()
    row = db_session.get(Amenity, amenity)
    assert row is not None and (row.min_advance_hours, row.max_advance_days) == (24, 60)


@pytest.mark.parametrize(
    "values",
    [
        {"min_advance_hours": "0", "max_advance_days": "1", "max_per_unit_per_month": "1",
         "cancel_until_hours": "0"},
        {"min_advance_hours": "168", "max_advance_days": "365", "max_per_unit_per_month": "31",
         "cancel_until_hours": "168"},
        {"max_per_unit_per_month": ""},  # «Sin límite»
    ],
)  # fmt: skip
def test_sum_numbers_accept_the_list(
    logged_in: Panel, db_session: Session, amenity: int, values: dict[str, str]
) -> None:
    assert _sum_post(logged_in, amenity, **values).status_code == 302
    db_session.expire_all()
    row = db_session.get(Amenity, amenity)
    assert row is not None
    limit = values.get("max_per_unit_per_month")
    assert row.max_per_unit_per_month == (int(limit) if limit else None)


def test_sum_stored_value_off_the_list_is_kept(
    logged_in: Panel, db_session: Session, amenity: int
) -> None:
    row = db_session.get(Amenity, amenity)
    assert row is not None
    row.cancel_until_hours = 200
    db_session.commit()
    page = logged_in.client.get(f"/admin/amenities/{amenity}/config").text
    assert _selected(page, "cancel_until_hours") == ("200", "200 horas")
    assert _sum_post(logged_in, amenity, cancel_until_hours="200").status_code == 302
    db_session.expire_all()
    assert db_session.get(Amenity, amenity).cancel_until_hours == 200


# --- Respuestas rápidas: a number, not a list ------------------------------------------------


@pytest.mark.parametrize(("order", "ok"), [("-1", False), ("1000", False), ("1.5", False),
                                           ("", False), ("0", True), ("999", True)])  # fmt: skip
def test_quick_reply_order(logged_in: Panel, db_session: Session, order: str, ok: bool) -> None:
    data = {"title": "Saludo", "content": "Hola", "sort_order": order, "active": "y",
            "save": "Save"}  # fmt: skip
    response = logged_in.client.post("/admin/quick-reply/create", data=data, follow_redirects=False)
    assert (response.status_code == 302) is ok
    assert (db_session.scalar(select(QuickReply)) is not None) is ok
    if not ok:
        assert "un número entero de 0 a 999" in _field_error(response.text, "sort_order")


# --- The browser never validates by itself ---------------------------------------------------


def _templates() -> list[Path]:
    return sorted((ADMIN_DIR / "templates").rglob("*.html"))


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_every_form_has_novalidate(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    missing = [tag for tag in re.findall(r"<form\b[^>]*>", text, re.S) if "novalidate" not in tag]
    assert missing == []


def test_the_script_never_shows_the_browsers_bubbles() -> None:
    js = (ADMIN_DIR / "static" / "panel.js").read_text(encoding="utf-8")
    assert "reportValidity" not in js and "setCustomValidity" not in js
    dialog = (ADMIN_DIR / "templates" / "_confirm_dialog.html").read_text(encoding="utf-8")
    assert 'id="confirm-error" class="field-error" role="alert" hidden' in dialog


@pytest.mark.parametrize("url", ["/admin/claim-settings/edit/1", "/admin/quick-reply/create",
                                 "/admin/building/list", "/admin/users"])  # fmt: skip
def test_rendered_forms_have_novalidate(logged_in: Panel, url: str) -> None:
    page = logged_in.client.get(url).text
    forms = re.findall(r"<form\b[^>]*>", page, re.S)
    assert [form for form in forms if "novalidate" not in form] == []


def test_the_login_has_novalidate(panel: Panel) -> None:
    page = panel.client.get("/admin/login").text
    assert all("novalidate" in form for form in re.findall(r"<form\b[^>]*>", page, re.S))


@pytest.mark.parametrize(
    ("url", "data", "message"),
    [
        ("/admin/users", {"display_name": "", "username": "", "role": "operator",
                          "password": "", "password2": ""}, "Escribí el nombre"),
        ("/admin/amenities/new", {"building_id": ""}, "Elegí un edificio."),
        ("/admin/quick-reply/create", {"title": "", "content": "", "sort_order": "1",
                                       "save": "Save"}, "Este campo es obligatorio."),
    ],
)  # fmt: skip
def test_empty_fields_get_spanish_messages_from_the_server(
    logged_in: Panel, url: str, data: dict[str, str], message: str
) -> None:
    response = logged_in.client.post(url, data=data, follow_redirects=True)
    assert message in response.text
