"""«Días cerrados» (Administración → Bot): only admins add and remove them, both audited, and
the bot and the claims use them right away. Invented data; the clock of the hours is fixed."""

from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.bot.bot_config import load_bot_config
from app.bot.prompts import handoff_notice
from app.claims.claim_config import load_claim_config
from app.db.models import ClosedDay, ClosedDayKind
from tests.admin.conftest import ADMIN, Panel, admin_settings

TZ = ZoneInfo("America/Argentina/Cordoba")
CHRISTMAS_EVE = date(2026, 12, 24)  # Thursday
HOLIDAY = date(2026, 10, 12)  # Monday, Día del Respeto a la Diversidad Cultural
FORM: dict[str, Any] = {
    "day": "2026-12-24",
    "kind": "closed",
    "reason": "Cierre del estudio",
    "save": "Save",
}


def _create(panel: Panel, **change: str) -> Any:
    return panel.client.post(
        "/admin/closed-day/create", data={**FORM, **change}, follow_redirects=False
    )


def _rows(panel: Panel) -> list[tuple[date, ClosedDayKind, str]]:
    panel.session.expire_all()
    return [(c.day, c.kind, c.reason) for c in panel.session.scalars(select(ClosedDay))]


def test_it_is_in_the_bot_block_with_the_next_holidays(logged_in: Panel) -> None:
    page = logged_in.client.get("/admin/closed-day/create").text
    assert "Los feriados nacionales ya cuentan solos" in page
    assert "El estudio cierra" in page and "Feriado: el estudio abre igual" in page
    assert logged_in.client.get("/admin/closed-day/list").status_code == 200


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

    cfg = load_bot_config(logged_in.session, admin_settings())
    assert not cfg.hours.is_open(morning)
    assert "Hoy el estudio está cerrado" in handoff_notice(morning, cfg.hours)
    claims = load_claim_config(logged_in.session, "America/Argentina/Cordoba")
    assert not claims.hours.is_open(morning)


def test_removing_it_is_audited(logged_in: Panel) -> None:
    _create(logged_in)
    [row_id] = logged_in.session.scalars(select(ClosedDay.id))
    response = logged_in.client.delete(f"/admin/closed-day/delete?pks={row_id}")
    assert response.status_code == 200
    assert _rows(logged_in) == []
    [event] = logged_in.admin_events("closed_day_deleted")
    assert event["closed_day_id"] == row_id and event["admin_user"] == ADMIN
    cfg = load_bot_config(logged_in.session, admin_settings())
    assert cfg.hours.is_open(datetime.combine(CHRISTMAS_EVE, time(10), TZ))


def test_a_national_holiday_marked_open_anyway(logged_in: Panel) -> None:
    response = _create(logged_in, day="2026-10-12", kind="open_anyway", reason="")
    assert response.status_code == 302
    [(day, kind, reason)] = _rows(logged_in)
    assert (day, kind) == (HOLIDAY, ClosedDayKind.OPEN_ANYWAY)
    assert reason  # the holiday's name when left blank
    cfg = load_bot_config(logged_in.session, admin_settings())
    assert cfg.hours.is_open(datetime.combine(HOLIDAY, time(10), TZ))
    assert logged_in.admin_events("closed_day_created")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"kind": "open_anyway"}, "no es feriado nacional"),
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


def test_an_operator_can_neither_see_nor_change_them(operator: Panel) -> None:
    operator.session.add(ClosedDay(day=CHRISTMAS_EVE, reason="Cierre inventado"))
    operator.session.commit()
    [row_id] = operator.session.scalars(select(ClosedDay.id))
    assert operator.client.get("/admin/closed-day/list").status_code == 403
    assert _create(operator, day="2026-12-31").status_code == 403
    assert operator.client.delete(f"/admin/closed-day/delete?pks={row_id}").status_code == 403
    assert operator.session.scalar(select(func.count()).select_from(ClosedDay)) == 1
    assert operator.admin_events() == []
    assert "Días cerrados" not in operator.client.get("/admin/pending").text
