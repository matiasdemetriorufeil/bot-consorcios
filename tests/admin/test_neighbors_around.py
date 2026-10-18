"""What surrounds «Vecinos» (5h.4): pending verifications approved from it, links from Teléfonos
and Conversaciones, and changes of several steps that are all or nothing. Invented data only."""

import html
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin import neighbors, pending
from app.admin.help import CONFIRM
from app.bot.identity import approve_verification_request
from app.db.models import (
    BotEvent,
    DataSource,
    Person,
    PersonRole,
    Phone,
    UnitPerson,
    VerificationRequest,
    VerificationRequestStatus,
)
from tests.admin.conftest import OPERATOR, Panel
from tests.admin.test_neighbors import FETCH, roster_fixture  # noqa: F401 (the fixture «roster»)
from tests.admin.wa_data import conversation

NEW_PHONE = "+5493515550901"


def _request(session: Session, unit_id: int, e164: str = NEW_PHONE) -> VerificationRequest:
    request = VerificationRequest(phone_e164=e164, unit_id=unit_id, claimed_name="Nora Pide")
    session.add(request)
    session.commit()
    return request


def _post(panel: Panel, url: str, data: dict) -> dict:
    response = panel.client.post(url, data=data, headers=FETCH)
    assert response.status_code == 200
    return response.json()


def _get(panel: Panel, url: str, **params) -> str:
    response = panel.client.get(url, params=params)
    assert response.status_code == 200
    return html.unescape(response.text)


def _person(session: Session, name: str) -> Person | None:
    session.expire_all()
    return session.scalar(select(Person).where(Person.full_name == name))


# --- 1. Verificaciones -----------------------------------------------------------------------


def test_adding_the_number_approves_its_pending_verification(
    operator: Panel, roster, db_session: Session
) -> None:
    request = _request(db_session, roster["tenth"])
    assert pending.counts(db_session)["verifications"] == 1

    reply = _post(
        operator,
        "/admin/neighbors/add/save",
        {"unit_id": roster["tenth"], "role": "owner", "full_name": "Nora Pide",
         "phone": "351 555-0901", "back": ""},
    )  # fmt: skip

    assert reply["ok"] is True
    assert "También quedó aprobada la verificación pendiente" in reply["message"]
    db_session.expire_all()
    nora = _person(db_session, "Nora Pide")
    assert (request.status, request.person_id, request.resolved_by) == (
        VerificationRequestStatus.APPROVED, nora.id, OPERATOR,
    )  # fmt: skip
    assert pending.counts(db_session)["verifications"] == 0
    event = db_session.scalar(
        select(BotEvent).where(BotEvent.event_type == "operator_verification_resolved")
    )
    assert event.payload["via"] == "vecinos" and event.payload["resolved_by"] == OPERATOR


def test_adding_or_moving_a_phone_also_approves_it(
    operator: Panel, roster, db_session: Session
) -> None:
    rita = _person(db_session, "Rita Familiar")
    first = _request(db_session, roster["pb"])
    keys = {"person_id": rita.id, "unit_id": roster["pb"], "role": "resident", "back": ""}
    assert _post(operator, "/admin/neighbors/phones/add", {**keys, "phone": NEW_PHONE})["ok"]

    ana_phone = db_session.scalar(select(Phone).where(Phone.e164 == "+5493515550101"))
    second = _request(db_session, roster["pb"], ana_phone.e164)
    moved = _post(operator, f"/admin/neighbors/phones/{ana_phone.id}/move", keys)

    assert "verificación pendiente" in moved["message"]
    db_session.expire_all()
    assert first.status == second.status == VerificationRequestStatus.APPROVED


def test_approving_a_verification_still_works_and_shows_in_vecinos(
    operator: Panel, roster, db_session: Session
) -> None:
    ana = _person(db_session, "Ana Inventada")
    request = _request(db_session, roster["pb"])
    approve_verification_request(db_session, request.id, ana.id, OPERATOR)

    results = _get(operator, "/admin/neighbors", building_id=roster["torre"])
    # Nothing next to the number: where it came from is only the person's «Origen» column.
    assert re.search(r'<span class="text-nowrap">351 555-0901</span>\s*</span>', results)
    assert "neighbor-phone-origin" not in results


# --- 2. Teléfonos ----------------------------------------------------------------------------


def test_the_person_of_a_phone_opens_in_vecinos(operator: Panel, roster, db_session) -> None:
    phones = _get(operator, "/admin/phones", tab="all")
    ana = _person(db_session, "Ana Inventada")
    link = f'/admin/neighbors?building_id={roster["torre"]}&mark={ana.id}">Ana Inventada</a>'
    assert link in phones

    page = _get(operator, "/admin/neighbors", building_id=roster["torre"], mark=ana.id)
    assert page.count("data-marked") == 1
    marked = page.split('<tr class="neighbor-marked" data-marked>', 1)[1].split("</tr>", 1)[0]
    assert "Ana Inventada" in marked


def test_the_marked_person_opens_on_their_page(
    operator: Panel, roster, db_session: Session, monkeypatch
) -> None:
    monkeypatch.setattr(neighbors, "PAGE_SIZE", 2)
    carlos = _person(db_session, "Carlos Revisar")  # 1° B, the second unit
    dora = _person(db_session, "Dora Conflicto")  # 2° A, the third: page 2
    first = _get(operator, "/admin/neighbors", building_id=roster["torre"], mark=carlos.id)
    assert "data-marked" in first and 'aria-current="page">1<' in first
    page = _get(operator, "/admin/neighbors", building_id=roster["torre"], mark=dora.id)
    assert "data-marked" in page and 'aria-current="page">2<' in page


def test_unlink_says_it_does_not_come_back() -> None:
    text = CONFIRM["unlink"]["text"]
    assert "no vuelve a aparecer" in text and "Quitados a mano" in text
    assert "a la mañana siguiente vuelve" not in text


# --- 3. Conversaciones -----------------------------------------------------------------------


def test_the_contact_card_offers_vecinos(operator: Panel, roster, db_session: Session) -> None:
    unknown = conversation(db_session, "5493515550901")
    known = conversation(db_session, "5493515550101")  # Ana
    db_session.commit()

    page = _get(operator, f"/admin/conversations/{unknown.id}")
    assert "Agregar a Vecinos" in page and "/admin/neighbors/add?phone=%2B5493515550901" in page

    page = _get(operator, f"/admin/conversations/{known.id}")
    ana = _person(db_session, "Ana Inventada")
    assert f"/admin/neighbors?building_id={roster['torre']}&mark={ana.id}" in page
    assert "Ver en Vecinos" in page and "Agregar a Vecinos" not in page


def test_agregar_a_vecinos_asks_the_building_then_keeps_the_phone(
    operator: Panel, roster, db_session: Session
) -> None:
    first = _get(operator, "/admin/neighbors/add", phone="+5493515550901")
    assert "Agregar a Vecinos" in first and "Teléfono 351 555-0901" in first
    assert '<option value="TORRE VECINOS">' in first

    second = _get(operator, "/admin/neighbors/add", phone="351 555-0901", building="torre vecinos")
    assert 'name="unit" list="units-add-top-new"' in second
    assert 'value="351 555-0901"' in second
    assert "data-fetch" not in second.split('id="neighbor-add-top-new"', 1)[1].split(">", 1)[0]

    response = operator.client.post(
        "/admin/neighbors/add/save",
        data={"building_id": roster["torre"], "unit": "10° C", "role": "tenant",
              "full_name": "Nora Nueva", "phone": "351 555-0901", "back": ""},
        follow_redirects=False,
    )  # fmt: skip
    nora = _person(db_session, "Nora Nueva")
    assert response.status_code == 302
    assert response.headers["location"].endswith(
        f"/admin/neighbors?building_id={roster['torre']}&mark={nora.id}"
    )


# --- 6. All or nothing -------------------------------------------------------------------------


def test_editing_is_all_or_nothing(operator: Panel, roster, db_session: Session) -> None:
    bruno = _person(db_session, "Bruno Inquilino")  # tenant of PB A
    # Bruno is also its resident: changing his role to that fails (the step in the middle).
    db_session.add(
        UnitPerson(unit_id=roster["pb"], person_id=bruno.id, role=PersonRole.RESIDENT,
                   source=DataSource.MANUAL)
    )  # fmt: skip
    db_session.commit()

    reply = _post(
        operator,
        f"/admin/neighbors/{bruno.id}/save",
        {"unit_id": roster["pb"], "role": "tenant", "full_name": "Bruno Renombrado",
         "new_role": "resident", "new_unit_id": roster["tenth"], "back": ""},
    )  # fmt: skip

    assert reply["ok"] is False and "ya está en esa unidad con ese rol" in reply["message"]
    db_session.expire_all()
    same = db_session.get(Person, bruno.id)
    assert same.full_name == "Bruno Inquilino"  # the first step was undone too
    assert {(k.unit_id, k.role) for k in same.units} == {
        (roster["pb"], PersonRole.TENANT), (roster["pb"], PersonRole.RESIDENT),
    }  # fmt: skip
    assert operator.admin_events() == []


def test_adding_with_a_failed_move_creates_nobody(operator: Panel, roster, db_session) -> None:
    reply = _post(
        operator,
        "/admin/neighbors/add/save",
        {"unit_id": roster["tenth"], "role": "owner", "full_name": "Nora Nueva",
         "move_phone_id": 999999, "back": ""},
    )  # fmt: skip

    assert reply["ok"] is False and reply["message"] == "Ese número ya no está en el padrón."
    assert _person(db_session, "Nora Nueva") is None
    assert operator.admin_events() == []
