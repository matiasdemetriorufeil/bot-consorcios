"""«Vecinos»: agregar, editar y quitar personas (5h.3), through the page and its actions without
reloading. Everything goes through app.admin.neighbors_service. Invented data only."""

import html
import re

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admin.formatting import phone
from app.admin.help import EMPTY, NEIGHBOR_OWNER_NOTE
from app.bot.identity import can_view_unit_finance, identify_by_phone
from app.db.models import (
    DataSource,
    Person,
    PersonRole,
    Phone,
    Provider,
    RosterSuppression,
    SuppressionKind,
    UnitPerson,
)
from app.sync.roster import sync_roster
from tests.admin.conftest import Panel
from tests.admin.test_neighbors import (
    ANA_PHONE,
    FETCH,
    roster_fixture,  # noqa: F401 (the fixture «roster»)
)
from tests.sync.test_roster_sync import ANA, FakeSource, row

NEW_PHONE = "+5493515550901"


def _person(session: Session, name: str) -> Person | None:
    session.expire_all()
    return session.scalar(select(Person).where(Person.full_name == name))


def _post(panel: Panel, url: str, data: dict) -> dict:
    response = panel.client.post(url, data=data, headers=FETCH)
    assert response.status_code == 200
    return response.json()


def _get(panel: Panel, url: str, **params) -> str:
    response = panel.client.get(url, params=params)
    assert response.status_code == 200
    return html.unescape(response.text)


def _add(panel: Panel, unit_id: int, **data) -> dict:
    values = {"unit_id": unit_id, "role": "owner", "back": "building_id=0", **data}
    return _post(panel, "/admin/neighbors/add/save", values)


def _back(roster: dict) -> str:
    return f"building_id={roster['torre']}"


# --- Agregar persona -------------------------------------------------------------------------


def test_add_a_new_person_by_hand(operator: Panel, roster, db_session: Session) -> None:
    reply = _add(
        operator, roster["tenth"], full_name="Nora Nueva", phone=["0351 15 555-0901", ""],
        back=_back(roster),
    )  # fmt: skip

    assert reply["ok"] is True and reply["message"] == "Persona agregada."
    results = html.unescape(reply["replace"]["#neighbors-results"])
    assert "Nora Nueva" in results and phone(NEW_PHONE) in results
    assert "3 de 4 unidades" in re.sub(r"</?strong>", "", results)  # the counter, redrawn
    nora = _person(db_session, "Nora Nueva")
    link = db_session.get(UnitPerson, (roster["tenth"], nora.id, PersonRole.OWNER))
    assert link.source == DataSource.MANUAL
    assert identify_by_phone(db_session, NEW_PHONE).person_id == nora.id
    assert can_view_unit_finance(db_session, NEW_PHONE, roster["tenth"])
    [event] = operator.admin_events("neighbor_added")
    assert "Nora" not in str(event)


def test_add_someone_already_in_the_roster(operator: Panel, roster, db_session: Session) -> None:
    elena = _person(db_session, "Elena Sol")
    similar = _get(operator, "/admin/neighbors/similar", name="elena  sol")
    assert "¿Es la misma persona que está en SOL VECINOS · PB A (propietario/a)?" in similar
    assert f'data-pick-person="{elena.id}"' in similar
    assert _get(operator, "/admin/neighbors/similar", name="ele").strip() == ""  # too short
    assert EMPTY["neighbors:similar"] in _get(
        operator, "/admin/neighbors/similar", name="Zoe Nadie"
    )

    reply = _add(operator, roster["tenth"], person_id=elena.id, role="resident", full_name="x")

    assert reply["ok"] is True
    db_session.expire_all()
    assert db_session.get(UnitPerson, (roster["tenth"], elena.id, PersonRole.RESIDENT))
    count = db_session.scalar(select(func.count()).select_from(Person))
    assert count == 6  # nobody new


def test_the_form_asks_with_the_owner_sentence(operator: Panel, roster) -> None:
    form = _get(operator, "/admin/neighbors/add", unit_id=roster["tenth"], fragment="1")
    assert "Agregar persona en 10° C" in form and "<!DOCTYPE" not in form
    assert f'data-owner-note="{NEIGHBOR_OWNER_NOTE}"' in form
    assert re.search(r'data-confirm-text="[^"]*' + re.escape(NEIGHBOR_OWNER_NOTE), form)

    page = _get(operator, "/admin/neighbors/add", building_id=roster["torre"])  # without JS
    assert "<!DOCTYPE" in page and 'list="units-add-top-new"' in page


def test_a_phone_without_area_code_is_not_saved(operator: Panel, roster, db_session) -> None:
    hint = _get(operator, "/admin/neighbors/phone-check", raw="555-0901")
    assert "Falta la característica (por ejemplo 351)." in hint
    ok = _get(operator, "/admin/neighbors/phone-check", raw="0351 15 555-0901")
    assert "Se va a guardar como <strong>351 555-0901</strong>" in ok

    reply = _add(operator, roster["tenth"], full_name="Nora Nueva", phone="555-0901")

    assert reply == {**reply, "ok": False, "message": "Falta la característica (por ejemplo 351)."}
    assert _person(db_session, "Nora Nueva") is None


def test_a_phone_of_someone_else_is_moved_only_when_confirmed(
    operator: Panel, roster, db_session: Session
) -> None:
    hint = _get(operator, "/admin/neighbors/phone-check", raw="351 555-0101")
    assert "Este número es de Ana Inventada (TORRE VECINOS · PB A (propietario/a))." in hint
    assert "data-move-phone=" in hint and "data-cancel-phone" in hint

    # «Cancelar» (the number still sent): nothing is saved.
    refused = _add(operator, roster["tenth"], full_name="Nora Nueva", phone="351 555-0101")
    assert refused["ok"] is False and refused["message"].startswith("Este número es de Ana")
    assert _person(db_session, "Nora Nueva") is None

    # «Pasarlo a esta persona»: the number is moved instead of added.
    ana_phone = db_session.scalar(select(Phone).where(Phone.e164 == ANA_PHONE))
    moved = _add(operator, roster["tenth"], full_name="Nora Nueva", move_phone_id=ana_phone.id)
    assert moved["ok"] is True
    nora = _person(db_session, "Nora Nueva")
    db_session.refresh(ana_phone)
    assert (ana_phone.person_id, ana_phone.source) == (nora.id, DataSource.MANUAL)
    assert db_session.scalar(
        select(RosterSuppression).where(RosterSuppression.kind == SuppressionKind.PHONE)
    )  # from ConsorPlus: it does not come back to Ana at night


def test_a_providers_whatsapp_warns_and_saves(operator: Panel, roster, db_session) -> None:
    db_session.add(Provider(name="Ascensores Ficticios", whatsapp_e164=NEW_PHONE))
    db_session.commit()

    hint = _get(operator, "/admin/neighbors/phone-check", raw="351 555-0901")
    assert "Ascensores Ficticios" in hint

    reply = _add(operator, roster["tenth"], full_name="Nora Nueva", phone="351 555-0901")
    assert reply["ok"] is True and "Ascensores Ficticios" in reply["message"]
    assert _person(db_session, "Nora Nueva") is not None


# --- Editar ----------------------------------------------------------------------------------


def test_editing_a_consorplus_name_does_not_duplicate_it_at_night(
    operator: Panel, db_session: Session
) -> None:
    source = FakeSource({"77": [row("1", ANA, building="077")]})
    sync_roster(db_session, source, ["77"])
    ana = _person(db_session, "LOPEZ ANA")
    unit_id = ana.units[0].unit_id

    form = _get(
        operator, f"/admin/neighbors/{ana.id}/edit", unit_id=unit_id, role="owner", fragment="1"
    )
    assert (
        "En ConsorPlus figura como LOPEZ ANA. Los cambios que hagas acá no se pasan a ConsorPlus."
        in form
    )

    reply = _post(
        operator,
        f"/admin/neighbors/{ana.id}/save",
        {"unit_id": unit_id, "role": "owner", "full_name": "Ana López", "back": ""},
    )
    assert reply["ok"] is True and reply["message"] == "Cambios guardados."
    sync_roster(db_session, source, ["77"])

    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(Person)) == 1
    renamed = db_session.get(Person, ana.id)
    assert (renamed.full_name, renamed.consorplus_name) == ("Ana López", "LOPEZ ANA")
    assert [link.unit_id for link in renamed.units] == [unit_id]


def test_change_role_and_unit(operator: Panel, roster, db_session: Session) -> None:
    carlos = _person(db_session, "Carlos Revisar")
    first_unit = carlos.units[0].unit_id

    reply = _post(
        operator,
        f"/admin/neighbors/{carlos.id}/save",
        {
            "unit_id": first_unit, "role": "owner", "full_name": "Carlos Revisar",
            "new_role": "tenant", "new_unit_id": roster["tenth"], "back": _back(roster),
        },
    )  # fmt: skip

    assert reply["ok"] is True
    db_session.expire_all()
    links = {(k.unit_id, k.role, k.source) for k in db_session.get(Person, carlos.id).units}
    assert links == {(roster["tenth"], PersonRole.TENANT, DataSource.MANUAL)}
    results = re.sub(r"</?strong>", "", html.unescape(reply["replace"]["#neighbors-results"]))
    assert "1 de 4 unidades" in results  # 1° B lost its owner's phone
    actions = [e["action"] for e in operator.admin_events()]
    assert actions == ["neighbor_role_changed", "neighbor_moved"]


def test_phones_of_the_edit_form_keep_it_open(operator: Panel, roster, db_session) -> None:
    rita = _person(db_session, "Rita Familiar")
    keys = {"person_id": rita.id, "unit_id": roster["pb"], "role": "resident", "back": ""}

    added = _post(operator, "/admin/neighbors/phones/add", {**keys, "phone": "351 555-0901"})
    assert added["ok"] is True and added["message"] == "Teléfono agregado."
    editor = html.unescape(added["replace"][f'[data-editor="{roster["pb"]}"]'])
    assert "Editar a Rita Familiar" in editor and phone(NEW_PHONE) in editor

    taken = _post(operator, "/admin/neighbors/phones/add", {**keys, "phone": "351 555-0101"})
    assert taken["ok"] is False and "Este número es de Ana Inventada" in taken["message"]
    hint = _get(
        operator, "/admin/neighbors/phone-check", raw="351 555-0101", mode="edit",
        person_id=rita.id, unit_id=roster["pb"], role="resident",
    )  # fmt: skip
    ana_phone = db_session.scalar(select(Phone).where(Phone.e164 == ANA_PHONE))
    assert f"/admin/neighbors/phones/{ana_phone.id}/move" in hint

    moved = _post(operator, f"/admin/neighbors/phones/{ana_phone.id}/move", keys)
    assert moved["ok"] is True
    removed = _post(operator, f"/admin/neighbors/phones/{ana_phone.id}/remove", keys)
    assert removed["ok"] is True and removed["message"] == "Teléfono quitado."
    db_session.expire_all()
    assert {p.e164 for p in db_session.get(Person, rita.id).phones} == {
        NEW_PHONE, "+5493515550102",
    }  # fmt: skip


# --- Quitar ----------------------------------------------------------------------------------


def test_the_remove_dialog_says_what_happens(operator: Panel, roster) -> None:
    page = _get(operator, "/admin/neighbors", building_id=roster["torre"])
    assert (
        "El bot va a dejar de reconocer a Ana Inventada como propietario/a de PB A y se borran sus "
        "teléfonos. No vuelve a aparecer con la sincronización. Para traerlo de nuevo, pedile a "
        "un admin: Quitados a mano › Restaurar." in page
    )
    # Loaded by hand: nothing to suppress.
    assert 'a Rita Familiar como familiar u otro de PB A y se borran sus teléfonos."' in page


def test_the_admin_reads_where_to_restore(logged_in: Panel, roster) -> None:
    page = _get(logged_in, "/admin/neighbors", building_id=roster["torre"])
    assert (
        "No vuelve a aparecer con la sincronización. Para traerlo de nuevo: Quitados a mano" in page
    )


def test_remove_from_the_only_unit(operator: Panel, roster, db_session: Session) -> None:
    ana = _person(db_session, "Ana Inventada")
    reply = _post(
        operator, "/admin/neighbors/remove",
        {"unit_id": roster["pb"], "person_id": ana.id, "role": "owner", "back": _back(roster)},
    )  # fmt: skip

    assert reply["ok"] is True
    assert reply["message"] == "Quitada de la unidad. Se borraron sus teléfonos."
    assert _person(db_session, "Ana Inventada") is None
    assert db_session.scalar(select(Phone).where(Phone.e164 == ANA_PHONE)) is None
    assert reply["texts"]["[data-removed-count]"] == "2"  # its link and its phone


def test_remove_from_one_of_several_units(operator: Panel, roster, db_session: Session) -> None:
    elena = _person(db_session, "Elena Sol")
    _add(operator, roster["tenth"], person_id=elena.id, role="resident", full_name="x")

    reply = _post(
        operator, "/admin/neighbors/remove",
        {"unit_id": roster["tenth"], "person_id": elena.id, "role": "resident", "back": ""},
    )  # fmt: skip

    assert reply["ok"] is True and reply["message"] == "Quitada de la unidad."
    elena = _person(db_session, "Elena Sol")
    assert elena is not None and len(elena.units) == 1 and elena.phones


# --- Permissions and without JavaScript --------------------------------------------------------


def test_the_operator_page_never_posts_to_the_admin_actions(operator: Panel, roster) -> None:
    page = _get(operator, "/admin/neighbors", building_id=roster["torre"])
    actions = re.findall(r'<form method="POST" action="([^"]+)"', page)
    assert actions and not [a for a in actions if "refresh" in a or "restore" in a]
    assert "/admin/neighbors/remove" in " ".join(actions)
    assert 'data-open-editor="top"' in page  # «Agregar persona», also for her


def test_without_javascript_it_goes_back_to_the_list(operator: Panel, roster) -> None:
    response = operator.client.post(
        "/admin/neighbors/add/save",
        data={"unit_id": roster["tenth"], "role": "owner", "full_name": "Nora Nueva",
              "back": _back(roster)},
        follow_redirects=False,
    )  # fmt: skip
    assert response.status_code == 302
    nora = _person(operator.session, "Nora Nueva")  # 5h.4: back with her marked
    assert response.headers["location"].endswith(
        f"/admin/neighbors?building_id={roster['torre']}&mark={nora.id}"
    )


def test_without_login_nothing_changes(panel: Panel, roster, db_session: Session) -> None:
    response = panel.client.post(
        "/admin/neighbors/add/save",
        data={"unit_id": roster["tenth"], "role": "owner", "full_name": "Nora Nueva"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303) and "/admin/login" in response.headers["location"]
    assert _person(db_session, "Nora Nueva") is None


@pytest.mark.parametrize("url", ["/admin/neighbors/add", "/admin/neighbors/999/edit"])
def test_a_form_that_cannot_open(operator: Panel, roster, url: str) -> None:
    fragment = _get(operator, url, fragment="1", unit_id=999999, role="owner")
    assert 'role="alert"' in fragment
