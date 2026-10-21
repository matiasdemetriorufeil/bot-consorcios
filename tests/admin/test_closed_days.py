"""«Días cerrados» (Administración → Bot): the national holidays of the chosen year with their
state and «Abre igual», the days closed by hand, only admins change them, every change audited,
and the bot and the claims use them right away. Invented data; the panel's clock is fixed at
Wednesday 30/09/2026 (tests.admin.conftest.NOW)."""

import re
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.bot.bot_config import load_bot_config
from app.bot.prompts import handoff_notice
from app.business_hours import national_holidays
from app.claims.claim_config import load_claim_config
from app.db.models import ClosedDay, ClosedDayKind
from tests.admin.conftest import ADMIN, Panel, admin_settings

TZ = ZoneInfo("America/Argentina/Cordoba")
LIST = "/admin/closed-day/list"
CHRISTMAS_EVE = date(2026, 12, 24)  # Thursday
HOLIDAY = date(2026, 10, 12)  # Monday, a national holiday after the panel's today
PAST_HOLIDAY = date(2026, 5, 25)  # Monday, already past
FORM: dict[str, Any] = {"day": "2026-12-24", "reason": "Cierre del estudio", "save": "Save"}


def _create(panel: Panel, **change: str) -> Any:
    return panel.client.post(
        "/admin/closed-day/create", data={**FORM, **change}, follow_redirects=False
    )


def _open_anyway(panel: Panel, day: str, year: str = "2026") -> Any:
    return panel.client.post(
        "/admin/closed-day/open-anyway", data={"day": day, "year": year}, follow_redirects=False
    )


def _remove(panel: Panel, row_id: int | str, year: str = "2026") -> Any:
    return panel.client.post(
        "/admin/closed-day/remove", data={"id": str(row_id), "year": year}, follow_redirects=False
    )


def _rows(panel: Panel) -> list[tuple[date, ClosedDayKind, str]]:
    panel.session.expire_all()
    return [(c.day, c.kind, c.reason) for c in panel.session.scalars(select(ClosedDay))]


def _table(page: str, table_id: str) -> str:
    match = re.search(rf'<table[^>]*id="{table_id}"[^>]*>(.*?)</table>', page, re.S)
    return match.group(1) if match else ""


def _row(table: str, label: str) -> str:
    [row] = [r for r in table.split("<tr")[1:] if f">{label}<" in r]
    return row


def _page(panel: Panel, query: str = "") -> str:
    response = panel.client.get(LIST + query)
    assert response.status_code == 200
    return response.text


# --- The table of national holidays ----------------------------------------------------------


def test_the_table_lists_the_holidays_of_this_year(logged_in: Panel) -> None:
    page = _page(logged_in)
    assert "Feriados nacionales 2026" in page
    assert "si tiene que pasar a una persona avisa que se responde el próximo día hábil" in page
    upcoming, past = _table(page, "holidays"), _table(page, "holidays-past")
    all_2026 = national_holidays(2026)
    coming = [(d, n) for d, n in all_2026 if d >= date(2026, 9, 30)]
    assert upcoming.count("<tr") - 1 == len(coming)  # minus the header
    for _day, name in coming:
        assert name in upcoming
    row = _row(upcoming, "lunes 12/10")
    assert "Cerrado" in row and "state state-info" in row and ">Abre igual</button>" in row
    # The past ones, folded and without buttons.
    assert f"Ya pasaron ({len(all_2026) - len(coming)})" in page
    old = _row(past, "lunes 25/05")
    assert 'class="holiday-past"' in old and "<button" not in old


def test_the_year_selector_shows_the_next_year(logged_in: Panel) -> None:
    page = _page(logged_in, "?year=2027")
    assert "Feriados nacionales 2027" in page
    upcoming = _table(page, "holidays")
    for day, name in national_holidays(2027):
        assert f"{day:%d/%m}" in upcoming and name in upcoming
    assert "Ya pasaron" not in page
    assert 'href="https://testserver/admin/closed-day/list?year=2026"' in page
    # A year that is not offered: this one.
    assert "Feriados nacionales 2026" in _page(logged_in, "?year=2031")


def test_abre_igual_creates_and_removes_the_audited_row(logged_in: Panel) -> None:
    response = _open_anyway(logged_in, HOLIDAY.isoformat())
    assert response.status_code == 303
    assert response.headers["location"].endswith("/admin/closed-day/list?year=2026")
    [(day, kind, reason)] = _rows(logged_in)
    assert (day, kind) == (HOLIDAY, ClosedDayKind.OPEN_ANYWAY) and reason  # the holiday's name
    [event] = logged_in.admin_events("closed_day_created")
    assert event["admin_user"] == ADMIN
    assert (event["day"], event["kind"]) == ("2026-10-12", "open_anyway")

    page = _page(logged_in, "?year=2026")
    row = _row(_table(page, "holidays"), "lunes 12/10")
    assert "state state-ok" in row and "Abre igual</span>" in row
    assert ">Quitar abre igual</button>" in row
    assert "Cerrado" not in row
    assert "Listo: el 12/10/2026 el estudio abre igual." in page
    # Not in the table of the days closed by hand.
    assert "lunes 12/10" not in _table(page, "closed-days")
    cfg = load_bot_config(logged_in.session, admin_settings())
    assert cfg.hours.is_open(datetime.combine(HOLIDAY, time(10), TZ))

    [row_id] = logged_in.session.scalars(select(ClosedDay.id))
    assert _remove(logged_in, row_id).status_code == 303
    assert _rows(logged_in) == []
    [event] = logged_in.admin_events("closed_day_deleted")
    assert event["closed_day_id"] == row_id and event["kind"] == "open_anyway"
    row = _row(_table(_page(logged_in), "holidays"), "lunes 12/10")
    assert "Cerrado" in row and ">Abre igual</button>" in row
    cfg = load_bot_config(logged_in.session, admin_settings())
    assert not cfg.hours.is_open(datetime.combine(HOLIDAY, time(10), TZ))


def test_the_buttons_ask_first(logged_in: Panel) -> None:
    row = _row(_table(_page(logged_in), "holidays"), "lunes 12/10")
    assert 'data-confirm-title="¿El estudio abre igual este feriado?"' in row
    assert 'data-confirm-kind="primary"' in row


@pytest.mark.parametrize(
    ("day", "message"),
    [
        ("2026-10-13", "no es feriado nacional"),  # a Tuesday
        ("no-es-fecha", "No se entendió la fecha"),
    ],
)
def test_abre_igual_only_on_a_holiday(logged_in: Panel, day: str, message: str) -> None:
    assert _open_anyway(logged_in, day).status_code == 303
    assert message in _page(logged_in)
    assert _rows(logged_in) == []
    assert logged_in.admin_events("closed_day_created") == []


def test_abre_igual_twice_and_removing_what_is_gone(logged_in: Panel) -> None:
    _open_anyway(logged_in, HOLIDAY.isoformat())
    _page(logged_in)  # its «Listo»
    _open_anyway(logged_in, HOLIDAY.isoformat())  # a second tab, an old page
    assert "Ese día ya está cargado" in _page(logged_in)
    assert len(_rows(logged_in)) == 1
    assert _remove(logged_in, 999_999).status_code == 303
    assert "Ese día ya no estaba cargado." in _page(logged_in)
    assert logged_in.admin_events("closed_day_deleted") == []


# --- The days closed by hand -----------------------------------------------------------------


def test_the_form_has_no_list_of_holidays(logged_in: Panel) -> None:
    page = logged_in.client.get("/admin/closed-day/create").text
    assert "Próximos" not in page and 'name="kind"' not in page
    assert "no hace falta cargarlos" in page


def test_adding_a_closed_day_is_audited_and_used_right_away(logged_in: Panel) -> None:
    cfg = load_bot_config(logged_in.session, admin_settings())  # fills the caches
    morning = datetime.combine(CHRISTMAS_EVE, time(10), TZ)
    assert cfg.hours.is_open(morning)

    assert _create(logged_in).status_code == 302
    assert _rows(logged_in) == [(CHRISTMAS_EVE, ClosedDayKind.CLOSED, "Cierre del estudio")]
    [event] = logged_in.admin_events("closed_day_created")
    assert event["admin_user"] == ADMIN
    assert event["day"] == "2026-12-24" and event["kind"] == "closed"
    assert "Cierre del estudio" not in str(event)

    row = _row(_table(_page(logged_in), "closed-days"), "jueves 24/12/2026")
    assert "Cierre del estudio" in row and ">Quitar</button>" in row
    assert 'data-confirm-title="¿Quitar este día cerrado?"' in row

    cfg = load_bot_config(logged_in.session, admin_settings())
    assert not cfg.hours.is_open(morning)
    assert "Hoy el estudio está cerrado" in handoff_notice(morning, cfg.hours)
    claims = load_claim_config(logged_in.session, "America/Argentina/Cordoba")
    assert not claims.hours.is_open(morning)


def test_quitar_removes_it_audited(logged_in: Panel) -> None:
    _create(logged_in)
    [row_id] = logged_in.session.scalars(select(ClosedDay.id))
    assert _remove(logged_in, row_id).status_code == 303
    assert _rows(logged_in) == []
    [event] = logged_in.admin_events("closed_day_deleted")
    assert event["closed_day_id"] == row_id and event["admin_user"] == ADMIN
    page = _page(logged_in)
    assert "No hay días cerrados cargados a mano" in page
    cfg = load_bot_config(logged_in.session, admin_settings())
    assert cfg.hours.is_open(datetime.combine(CHRISTMAS_EVE, time(10), TZ))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"day": "2026-10-12"}, "ya es feriado nacional"),
        ({"reason": "  "}, "Escribí el motivo"),
    ],
)
def test_the_form_validates(logged_in: Panel, change: dict[str, str], message: str) -> None:
    response = _create(logged_in, **change)
    assert response.status_code == 400
    assert message in response.text
    assert _rows(logged_in) == []
    assert logged_in.admin_events("closed_day_created") == []


def test_the_same_day_twice_is_refused(logged_in: Panel) -> None:
    assert _create(logged_in).status_code == 302
    response = _create(logged_in, reason="Otro motivo")
    assert response.status_code == 400 and "Ese día ya está cargado" in response.text
    assert len(_rows(logged_in)) == 1


# --- Only admins ------------------------------------------------------------------------------


def test_an_operator_can_neither_see_nor_change_them(operator: Panel) -> None:
    operator.session.add(ClosedDay(day=CHRISTMAS_EVE, reason="Cierre inventado"))
    operator.session.commit()
    [row_id] = operator.session.scalars(select(ClosedDay.id))
    assert operator.client.get(LIST).status_code == 403
    assert operator.client.get(LIST + "?year=2027").status_code == 403
    assert _create(operator, day="2026-12-31").status_code == 403
    assert _open_anyway(operator, HOLIDAY.isoformat()).status_code == 403
    assert _remove(operator, row_id).status_code == 403
    assert operator.client.delete(f"/admin/closed-day/delete?pks={row_id}").status_code == 403
    assert operator.session.scalar(select(func.count()).select_from(ClosedDay)) == 1
    assert operator.admin_events() == []
    assert "Días cerrados" not in operator.client.get("/admin/pending").text
