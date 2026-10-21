"""«Teléfonos» → «A revisar»: the column «Por qué» (app.admin.phones.review_reason), deduced from
what is stored: in conflict (with the other person when the roster sync recorded it, see
tests/sync/test_roster_sync.py; both linked to Vecinos), assumed area code (the raw text
without it), loaded by the bot, other. A phone in conflict is left in the right person, never
approved. Invented data only."""

import re

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin.formatting import phone
from app.admin.pending import counts
from app.admin.phones import approve_phone, review_reason
from app.db.models import DataSource, Person, PersonRole, Phone
from tests.admin.conftest import ADMIN, Panel
from tests.bot import factories as f

AREA = "+5493515550411"  # came as "155550411": we added the 351
CONFLICT = "+5493515550412"
CONFLICT_UNKNOWN = "+5493515550413"  # flagged before the other person was recorded
BOT = "+5493515550414"
OTHER = "+5493515550415"


def _phone(session: Session, person: Person, e164: str, **values: object) -> Phone:
    row = Phone(person_id=person.id, e164=e164, **{"source": DataSource.CONSORPLUS, **values})
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def world(db_session: Session) -> dict[str, Phone]:
    s = db_session
    building = f.building(s, "099 TORRE MOTIVOS")
    unit_a, unit_b = f.unit(s, building, "1A"), f.unit(s, building, "3C")
    ana = f.person(s, "Ana Inventada")
    f.link(s, unit_a, ana)
    beto = f.person(s, "Beto Inventado")
    f.link(s, unit_b, beto, PersonRole.TENANT)
    rows = {
        "area": _phone(s, ana, AREA, raw="155550411", needs_review=True),
        "conflict": _phone(
            s, ana, CONFLICT, raw="351 555-0412", conflict=True, conflict_person_id=beto.id
        ),
        "unknown": _phone(s, beto, CONFLICT_UNKNOWN, raw="351 555-0413", conflict=True),
        "bot": _phone(s, beto, BOT, needs_review=True, source=DataSource.BOT_VERIFIED),
        "other": _phone(s, beto, OTHER, raw="351 555-0415", needs_review=True),
    }
    s.commit()
    return rows


def _row(page: str, e164: str) -> str:
    [row] = [r for r in page.split("<tr")[1:] if phone(e164) in r]
    return row


def _reason(row: str) -> str:
    match = re.search(r'<td data-label="Por qué"[^>]*>(.*?)</td>', row, re.S)
    assert match, "no «Por qué»"
    return match.group(1)


def test_each_reason_from_what_is_stored(db_session: Session, world: dict[str, Phone]) -> None:
    assert review_reason(world["area"]).kind == "area_code"
    assert review_reason(world["conflict"]).kind == "conflict"
    assert review_reason(world["unknown"]).kind == "conflict"
    assert review_reason(world["bot"]).kind == "bot"
    assert review_reason(world["other"]).kind == "other"
    # A conflict wins over the assumed area code: it does not identify anyone.
    world["area"].conflict = True
    assert review_reason(world["area"]).kind == "conflict"


def test_the_column_says_why_and_what_to_do(logged_in: Panel, world: dict[str, Phone]) -> None:
    page = logged_in.client.get("/admin/phones?tab=review").text
    area = _reason(_row(page, AREA))
    assert "Le agregamos el 351: confirmá que es un celular." in area
    conflict = _reason(_row(page, CONFLICT))
    assert re.search(
        r"También figura en <a [^>]*>Beto Inventado</a> \(TORRE MOTIVOS · 3C\)", conflict
    )
    assert "dejalo en la persona correcta." in conflict
    # Both people, linked to their building in Vecinos with the person marked.
    links = re.findall(r'href="[^"]*/admin/neighbors\?building_id=(\d+)&amp;mark=(\d+)"', conflict)
    assert {int(mark) for _, mark in links} == {
        world["conflict"].person_id,
        world["conflict"].conflict_person_id,
    }
    unknown = _reason(_row(page, CONFLICT_UNKNOWN))
    assert "figura también en otra persona" in unknown and "próxima lectura" in unknown
    assert "Lo asoció el bot" in _reason(_row(page, BOT))
    assert "Confirmá que el número es de esta persona." in _reason(_row(page, OTHER))
    # Never a code nor the internal names.
    assert "needs_review" not in page and "conflict_person" not in page


def test_a_conflict_has_no_box_nor_approve(logged_in: Panel, world: dict[str, Phone]) -> None:
    page = logged_in.client.get("/admin/phones?tab=review").text
    conflict = _row(page, CONFLICT)
    assert 'type="checkbox"' not in conflict and "/approve" not in conflict
    assert "/unlink" in conflict  # «Desvincular» from the wrong person
    assert 'type="checkbox"' in _row(page, AREA)
    # Not even forced by hand.
    assert not approve_phone(logged_in.session, world["conflict"].id, ADMIN)


def test_the_counters_include_the_conflicts(db_session: Session, world: dict[str, Phone]) -> None:
    assert counts(db_session)["phones"] == 5


def test_the_all_tab_has_no_reason(logged_in: Panel, world: dict[str, Phone]) -> None:
    page = logged_in.client.get("/admin/phones?tab=all").text
    table = page.split('id="phones"', 1)[1].split("</table>", 1)[0]
    assert "Por qué" not in table and "phone-reason" not in table


def test_moving_the_phone_clears_the_conflict(
    logged_in: Panel, world: dict[str, Phone], db_session: Session
) -> None:
    from app.admin import neighbors_service

    target = world["conflict"].conflict_person_id
    neighbors_service.move_phone(db_session, ADMIN, world["conflict"].id, target)
    row = db_session.scalar(select(Phone).where(Phone.e164 == CONFLICT))
    assert row is not None and not row.conflict and row.conflict_person_id is None
