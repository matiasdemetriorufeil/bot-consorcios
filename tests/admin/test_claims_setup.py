"""The claims' set-up in the panel (app.admin.claims_setup): providers, kinds of problem and
each building's table. Invented data only (fictitious providers, phones 351 555-01xx); who
may open these pages is in test_permissions."""

import html
import re
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admin import help
from app.db.models import (
    Building,
    BuildingClaimCategory,
    ClaimCategory,
    Provider,
    WaContact,
)
from tests.admin.conftest import Panel
from tests.bot import factories as f
from tests.claims import factories as cf

WA_1 = "+5493515550101"
WA_2 = "+5493515550102"


def _provider_form(**values: str) -> dict[str, str]:
    return {"name": "Ascensores Ficticios SRL", "active": "y", "save": "Save", **values}


def _text(page: str) -> str:
    return html.unescape(page)


def _count(session: Session, model: Any) -> int:
    session.expire_all()
    return session.scalar(select(func.count()).select_from(model)) or 0


# --- Providers ------------------------------------------------------------------------------


def test_create_a_provider_with_its_whatsapp_in_any_format(logged_in: Panel) -> None:
    response = logged_in.client.post(
        "/admin/provider/create",
        data=_provider_form(contact_name="Rosa Inventada", whatsapp_e164="0351 15 555-0101"),
        follow_redirects=True,
    )
    assert response.status_code == 200
    provider = logged_in.session.scalar(select(Provider))
    assert provider is not None and provider.whatsapp_e164 == WA_1
    # Shown as people write it, never as stored.
    assert "351 555-0101" in response.text and WA_1 not in response.text
    [event] = logged_in.admin_events("provider_created")
    assert event["provider_id"] == provider.id


def test_the_edit_form_shows_the_number_as_people_write_it(logged_in: Panel) -> None:
    provider = cf.provider(logged_in.session, "Plomería Inventada", WA_1)
    logged_in.session.commit()
    page = logged_in.client.get(f"/admin/provider/edit/{provider.id}").text
    assert 'value="351 555-0101"' in page


def test_an_invalid_whatsapp_is_refused(logged_in: Panel) -> None:
    response = logged_in.client.post(
        "/admin/provider/create", data=_provider_form(whatsapp_e164="555-0101")
    )
    assert response.status_code == 400
    assert "código de área" in _text(response.text)
    assert _count(logged_in.session, Provider) == 0


def test_a_whatsapp_of_another_active_provider_is_refused(logged_in: Panel) -> None:
    cf.provider(logged_in.session, "Ascensores Viejos Inventados", WA_1)
    logged_in.session.commit()
    response = logged_in.client.post(
        "/admin/provider/create",
        data=_provider_form(name="Otro Ficticio", whatsapp_e164="351 555-0101"),
    )
    assert response.status_code == 400
    assert "Ese WhatsApp ya lo tiene Ascensores Viejos Inventados" in _text(response.text)
    assert _count(logged_in.session, Provider) == 1

    # As an inactive provider it can be loaded.
    response = logged_in.client.post(
        "/admin/provider/create",
        data={"name": "Inactivo Ficticio", "whatsapp_e164": "351 555-0101", "save": "Save"},
        follow_redirects=True,
    )
    assert response.status_code == 200 and _count(logged_in.session, Provider) == 2


def test_a_whatsapp_of_an_owner_warns_but_saves(logged_in: Panel) -> None:
    f.person(logged_in.session, "Propietaria Inventada", phone=WA_1)
    logged_in.session.commit()
    response = logged_in.client.post(
        "/admin/provider/create",
        data=_provider_form(whatsapp_e164="351 555-0101"),
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert (
        "ese WhatsApp también es de alguien del padrón (propietario, inquilino o familiar)"
        in _text(response.text)
    )
    assert _count(logged_in.session, Provider) == 1


def test_a_whatsapp_of_a_contact_warns_on_edit(logged_in: Panel) -> None:
    provider = cf.provider(logged_in.session, "Plomería Inventada")
    logged_in.session.add(WaContact(phone_e164=WA_2, wa_id=WA_2[1:]))
    logged_in.session.commit()
    response = logged_in.client.post(
        f"/admin/provider/edit/{provider.id}",
        data=_provider_form(name="Plomería Inventada", whatsapp_e164="3515550102"),
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "está en Conversaciones" in _text(response.text)
    [event] = logged_in.admin_events("provider_updated")
    assert event["fields"] == ["whatsapp_e164"]


def test_deactivating_an_assigned_provider_warns(logged_in: Panel) -> None:
    session = logged_in.session
    building = f.building(session, "001 TORRE INVENTADA")
    provider = cf.provider(session, "Ascensores Ficticios SRL", WA_1)
    cf.assign(session, building.id, cf.category(session, "Ascensor inventado"), provider)
    session.commit()
    response = logged_in.client.post(
        f"/admin/provider/edit/{provider.id}",
        data={"name": "Ascensores Ficticios SRL", "whatsapp_e164": WA_1, "save": "Save"},
        follow_redirects=True,
    )
    assert "está asignado en 1 edificio(s)" in _text(response.text)
    session.expire_all()
    assert provider.active is False


def test_providers_cannot_be_deleted(logged_in: Panel) -> None:
    provider = cf.provider(logged_in.session, "Plomería Inventada")
    logged_in.session.commit()
    logged_in.client.delete(f"/admin/provider/delete?pks={provider.id}")
    assert _count(logged_in.session, Provider) == 1


def test_the_list_shows_buildings_and_problems(logged_in: Panel) -> None:
    session = logged_in.session
    torre = f.building(session, "001 TORRE INVENTADA")
    otra = f.building(session, "002 OTRA FICTICIA")
    provider = cf.provider(session, "Ascensores Ficticios SRL", WA_1)
    lift = cf.category(session, "Ascensor inventado", list_title="Ascensor")
    gate = cf.category(session, "Portón inventado", list_title="Portón")
    cf.assign(session, torre.id, lift, provider)
    cf.assign(session, otra.id, gate, provider)
    cf.assign(session, otra.id, lift, provider, enabled=False)
    session.commit()

    page = _text(logged_in.client.get("/admin/provider/list").text)

    assert "OTRA FICTICIA, TORRE INVENTADA" in page
    assert "Ascensor, Portón" in page
    assert "351 555-0101" in page
    assert help.PAGE_HELP["list:provider"] in page
    # Search by part of the number.
    found = logged_in.client.get("/admin/provider/list?search=5550101").text
    assert "Ascensores Ficticios SRL" in found


# --- Kinds of problem -----------------------------------------------------------------------


def _category_form(**values: str) -> dict[str, str]:
    return {
        "name": "Pérdida inventada", "list_title": "Pérdida", "scope": "unit",
        "sort_order": "10", "active": "y", "save": "Save", **values,
    }  # fmt: skip


def test_create_a_kind_of_problem(logged_in: Panel) -> None:
    response = logged_in.client.post(
        "/admin/claim-category/create",
        data=_category_form(urgent="y", follow_up_question="¿Hay agua en el piso?"),
        follow_redirects=True,
    )
    assert response.status_code == 200
    category = logged_in.session.scalar(select(ClaimCategory))
    assert category is not None and category.urgent and category.scope == "unit"
    assert "Una unidad" in _text(response.text)  # the list, in Spanish
    assert logged_in.admin_events("claim_category_created")


def test_the_list_limits_hold_on_the_server(logged_in: Panel) -> None:
    for values in ({"list_title": "x" * 25}, {"list_description": "y" * 73}):
        response = logged_in.client.post(
            "/admin/claim-category/create", data=_category_form(**values)
        )
        assert response.status_code == 400, values
    assert _count(logged_in.session, ClaimCategory) == 0


def test_the_form_counts_characters_and_explains_the_scope(logged_in: Panel) -> None:
    page = _text(logged_in.client.get("/admin/claim-category/create").text)
    assert re.search(r'name="list_title"[^>]*data-count="24"', page) or re.search(
        r'data-count="24"[^>]*name="list_title"', page
    )
    assert 'data-count="72"' in page
    assert help.SCOPE_HELP in page
    assert "Todo el edificio" in page and "Una unidad" in page
    assert ">building<" not in page and ">unit<" not in page


def test_kinds_of_problem_cannot_be_deleted(logged_in: Panel) -> None:
    category = cf.category(logged_in.session, "Pérdida inventada")
    logged_in.session.commit()
    logged_in.client.delete(f"/admin/claim-category/delete?pks={category.id}")
    assert _count(logged_in.session, ClaimCategory) == 1


# --- The switch in Edificios ----------------------------------------------------------------


def test_the_claims_switch_of_a_building(logged_in: Panel) -> None:
    building = f.building(logged_in.session, "001 TORRE INVENTADA")
    logged_in.session.commit()
    page = _text(logged_in.client.get(f"/admin/building/edit/{building.id}").text)
    assert "Reclamos por el bot" in page
    logged_in.client.post(
        f"/admin/building/edit/{building.id}",
        data={"name": building.name, "active": "y", "claims_bot_enabled": "y", "save": "Save"},
    )
    logged_in.session.expire_all()
    assert building.claims_bot_enabled is True
    [event] = logged_in.admin_events("building_updated")
    assert event["fields"] == ["claims_bot_enabled"]


# --- Each building's table ------------------------------------------------------------------


def _table(session: Session, building_id: int) -> dict[int, tuple[bool, int | None]]:
    session.expire_all()
    rows = session.scalars(
        select(BuildingClaimCategory).where(BuildingClaimCategory.building_id == building_id)
    )
    return {r.category_id: (r.enabled, r.provider_id) for r in rows}


def _setup(session: Session) -> dict[str, Any]:
    building = f.building(session, "001 TORRE INVENTADA")
    other = f.building(session, "002 OTRA FICTICIA")
    water = cf.category(session, "No hay agua inventada", list_title="Sin agua", sort_order=10)
    lift = cf.category(session, "Ascensor inventado", list_title="Ascensor", sort_order=20)
    lifts = cf.provider(session, "Ascensores Ficticios SRL", WA_1)
    plumber = cf.provider(session, "Plomería Inventada")
    cf.provider(session, "Proveedor Dado de Baja", active=False)
    session.commit()
    return {
        "building": building, "other": other, "water": water, "lift": lift, "lifts": lifts,
        "plumber": plumber,
    }  # fmt: skip


def test_the_list_of_buildings(logged_in: Panel) -> None:
    s = _setup(logged_in.session)
    cf.assign(logged_in.session, s["building"].id, s["water"])
    cf.assign(logged_in.session, s["building"].id, s["lift"], s["lifts"])
    logged_in.session.commit()
    page = _text(logged_in.client.get("/admin/building-claims").text)
    assert "TORRE INVENTADA" in page and "OTRA FICTICIA" in page
    assert help.PAGE_HELP["view-building-claims"] in page
    row = page.split("TORRE INVENTADA", 1)[1].split("</tr>", 1)[0]
    assert re.findall(r"<td[^>]*>(\d+)</td>", row)[:1] == ["2"]
    assert re.search(r'state-waiting">(<i [^>]*></i>)?1<', row)  # 5g: with its warning icon


def test_the_list_of_buildings_narrows(logged_in: Panel) -> None:
    """5g: the search and the filters only narrow the rows (Con pendientes: without provider)."""
    s = _setup(logged_in.session)
    cf.assign(logged_in.session, s["building"].id, s["water"])
    logged_in.session.commit()
    get = logged_in.client.get
    assert "OTRA FICTICIA" not in _text(get("/admin/building-claims?q=torre").text)
    assert "TORRE INVENTADA" in _text(get("/admin/building-claims?q=torre").text)
    pending = _text(get("/admin/building-claims?provider=pending").text)
    assert "TORRE INVENTADA" in pending and "OTRA FICTICIA" not in pending
    page = get("/admin/building-claims?bot=yes&q=nada").text
    assert "Ningún edificio coincide" in page and 'class="segmented-item active"' in page


def test_the_table_of_a_building(logged_in: Panel) -> None:
    s = _setup(logged_in.session)
    page = _text(logged_in.client.get(f"/admin/building-claims/{s['building'].id}").text)
    assert "Problemas y proveedores" in page
    assert "Lo atiende el estudio" in page
    assert "Ascensores Ficticios SRL" in page and "Plomería Inventada (sin WhatsApp)" in page
    assert "Proveedor Dado de Baja" not in page  # only active providers to choose
    assert help.SCOPE_HELP in page
    assert "Copiar de otro edificio" in page and "OTRA FICTICIA" in page
    assert "segunda lista" not in page


def test_save_the_whole_table(logged_in: Panel) -> None:
    s = _setup(logged_in.session)
    b, water, lift, lifts = s["building"], s["water"], s["lift"], s["lifts"]
    response = logged_in.client.post(
        f"/admin/building-claims/{b.id}",
        data={
            f"enabled_{water.id}": "on",
            f"provider_{water.id}": "",
            f"enabled_{lift.id}": "on",
            f"provider_{lift.id}": str(lifts.id),
        },  # fmt: skip
        follow_redirects=True,
    )
    assert response.status_code == 200
    page = _text(response.text)
    assert "Tabla guardada." in page
    assert "Estos los va a atender el estudio (no tienen proveedor): Sin agua." in page
    assert _table(logged_in.session, b.id) == {water.id: (True, None), lift.id: (True, lifts.id)}
    [event] = logged_in.admin_events("building_claims_saved")
    assert event["building_id"] == b.id
    assert sorted(event["category_ids"]) == sorted([water.id, lift.id])
    assert event["fields"] == ["enabled", "provider_id"]

    # Unchecked: disabled.
    logged_in.client.post(
        f"/admin/building-claims/{b.id}",
        data={
            f"provider_{water.id}": "",
            f"enabled_{lift.id}": "on",
            f"provider_{lift.id}": str(lifts.id),
        },  # fmt: skip
    )
    assert _table(logged_in.session, b.id)[water.id] == (False, None)


def test_a_provider_without_whatsapp_warns(logged_in: Panel) -> None:
    s = _setup(logged_in.session)
    cf.assign(logged_in.session, s["building"].id, s["lift"], s["plumber"])
    logged_in.session.commit()
    page = _text(logged_in.client.get(f"/admin/building-claims/{s['building'].id}").text)
    assert "Plomería Inventada no tiene WhatsApp cargado: el bot no le va a poder avisar" in page


def test_positions_sent_by_hand_are_ignored(logged_in: Panel) -> None:
    """The order is the general one: a position_<id> (or the old order_<id>) changes nothing,
    even a bad one."""
    s = _setup(logged_in.session)
    b, water, lift = s["building"], s["water"], s["lift"]
    response = logged_in.client.post(
        f"/admin/building-claims/{b.id}",
        data={
            f"enabled_{water.id}": "on",
            f"provider_{water.id}": "",
            f"position_{water.id}": "2",
            f"order_{water.id}": "-50",
            f"enabled_{lift.id}": "on",
            f"provider_{lift.id}": "",
            f"position_{lift.id}": "-1",
        },  # fmt: skip
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert _table(logged_in.session, b.id) == {water.id: (True, None), lift.id: (True, None)}
    titles = re.findall(
        r'<div class="fw-bold">([^<]+)</div>',
        logged_in.client.get(f"/admin/building-claims/{b.id}").text,
    )
    assert titles == ["Sin agua", "Ascensor"]  # «Tipos de problema»: water 10, lift 20


def test_the_table_follows_the_general_order(logged_in: Panel) -> None:
    s = _setup(logged_in.session)
    s["lift"].sort_order, s["water"].sort_order = 10, 20  # reordered in «Tipos de problema»
    logged_in.session.commit()
    page = logged_in.client.get(f"/admin/building-claims/{s['building'].id}").text
    table = page.split('id="building-claims-table"', 1)[1].split("</table>", 1)[0]
    assert re.findall(r'<div class="fw-bold">([^<]+)</div>', table) == ["Ascensor", "Sin agua"]
    # The place, only to read: no box, no buttons, nothing sent.
    places = re.findall(r'data-label="Orden"><span class="reorder-position">(\d+)</span>', table)
    assert places == ["1", "2"]
    assert "position_" not in table and "data-move" not in table and "data-reorder" not in table
    assert 'type="number"' not in table
    # Where to change it, for every building.
    assert "El orden se cambia en" in page and "y vale para todos los edificios." in page
    assert re.search(
        r'<a href="[^"]*/admin/claim-category/list">Reclamos › Tipos de problema</a>', page
    )


def test_more_than_ten_enabled_go_to_a_second_list(logged_in: Panel) -> None:
    session = logged_in.session
    building = f.building(session, "001 TORRE INVENTADA")
    for n in range(1, 13):
        category = cf.category(session, f"Problema inventado {n:02}", sort_order=n)
        cf.assign(session, building.id, category)
    session.commit()

    page = _text(logged_in.client.get(f"/admin/building-claims/{building.id}").text)

    assert "Hay 12 problemas habilitados" in page
    table = page.split('id="claims-table"', 1)[1]
    before, after = table.split("Estos aparecen en «Más opciones»")
    assert "Problema inventado 09" in before and "Problema inventado 10" not in before
    assert all(f"Problema inventado {n}" in after for n in (10, 11, 12))


def test_the_line_counts_only_the_enabled(logged_in: Panel) -> None:
    """Disabled problems in between do not count: the line goes before the 10th enabled."""
    session = logged_in.session
    building = f.building(session, "001 TORRE INVENTADA")
    for n in range(1, 13):
        category = cf.category(session, f"Problema inventado {n:02}", sort_order=n)
        cf.assign(session, building.id, category, enabled=n != 3)  # 11 enabled
    session.commit()
    page = _text(logged_in.client.get(f"/admin/building-claims/{building.id}").text)
    before, after = page.split("Estos aparecen en «Más opciones»")
    assert "Problema inventado 10" in before and "Problema inventado 11" in after


@pytest.mark.parametrize("enabled", [9, 10])
def test_no_line_while_they_fit_in_one_list(logged_in: Panel, enabled: int) -> None:
    session = logged_in.session
    building = f.building(session, "001 TORRE INVENTADA")
    for n in range(1, 13):
        category = cf.category(session, f"Problema inventado {n:02}", sort_order=n)
        cf.assign(session, building.id, category, enabled=n <= enabled)
    session.commit()
    page = _text(logged_in.client.get(f"/admin/building-claims/{building.id}").text)
    assert "Estos aparecen en «Más opciones»" not in page
    assert "problemas habilitados. WhatsApp muestra hasta 10" not in page


def test_copy_from_another_building(logged_in: Panel) -> None:
    """Copies what is enabled and who attends it; the order is the general one."""
    s = _setup(logged_in.session)
    b, other, lift, lifts = s["building"], s["other"], s["lift"], s["lifts"]
    cf.assign(logged_in.session, other.id, lift, lifts)
    logged_in.session.commit()

    page = _text(logged_in.client.get(f"/admin/building-claims/{b.id}").text)
    assert help.CONFIRM["copy_claims"]["text"] in page  # asks before
    assert "el orden" not in help.CONFIRM["copy_claims"]["text"]
    assert "Copia los habilitados y quién atiende cada uno." in page

    response = logged_in.client.post(
        f"/admin/building-claims/{b.id}/copy",
        data={"source_id": str(other.id)},
        follow_redirects=True,
    )
    assert "Copiado de OTRA FICTICIA" in _text(response.text)
    assert _table(logged_in.session, b.id) == {
        lift.id: (True, lifts.id),
        s["water"].id: (False, None),
    }
    [event] = logged_in.admin_events("building_claims_copied")
    assert event["building_id"] == b.id and event["from_building_id"] == other.id
    assert event["fields"] == ["enabled", "provider_id"]


def test_copy_needs_another_building(logged_in: Panel) -> None:
    s = _setup(logged_in.session)
    b = s["building"]
    response = logged_in.client.post(
        f"/admin/building-claims/{b.id}/copy", data={"source_id": str(b.id)}, follow_redirects=True
    )
    assert "Elegí otro edificio para copiar." in _text(response.text)
    assert _table(logged_in.session, b.id) == {}
    assert logged_in.admin_events("building_claims_copied") == []


def test_an_unknown_building(logged_in: Panel) -> None:
    missing = (logged_in.session.scalar(select(func.max(Building.id))) or 0) + 1
    assert logged_in.client.get(f"/admin/building-claims/{missing}").status_code == 404
