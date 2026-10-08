"""The claims' set-up (app.claims.setup) and its tables' constraints, against the Postgres test
database. All data is invented (fictitious providers, phones 351 555-01xx)."""

from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.claims.setup import (
    WHATSAPP_LIST_MAX_ROWS,
    Choice,
    SetupProblem,
    building_table,
    copy_building_table,
    normalize_provider_whatsapp,
    provider_with_whatsapp,
    save_building_table,
    split_for_whatsapp,
    table_warnings,
    whatsapp_in_use_warning,
)
from app.db.models import (
    BuildingClaimCategory,
    ClaimCategory,
    ClaimScope,
    Provider,
    WaContact,
)
from tests.bot import factories as f
from tests.claims import factories as cf

WA_1 = "+5493515550101"
WA_2 = "+5493515550102"
WA_3 = "+5493515550103"


def _fails(session: Session, *objects: Any) -> None:
    """Adding these objects breaks a constraint of the database (the test goes on)."""
    with pytest.raises(DBAPIError), session.begin_nested():
        session.add_all(objects)
        session.flush()


def _rows(session: Session, building_id: int) -> dict[int, tuple[bool, int | None, int]]:
    session.expire_all()
    rows = session.scalars(
        select(BuildingClaimCategory).where(BuildingClaimCategory.building_id == building_id)
    )
    return {r.category_id: (r.enabled, r.provider_id, r.sort_order) for r in rows}


# --- Constraints ----------------------------------------------------------------------------


def test_a_kind_of_problem_name_is_unique(db_session: Session) -> None:
    cf.category(db_session, "Pérdida inventada")
    _fails(
        db_session,
        ClaimCategory(name="Pérdida inventada", list_title="X", scope=ClaimScope.UNIT),
    )


def test_list_title_is_24_characters_at_most_and_not_empty(db_session: Session) -> None:
    cf.category(db_session, "Título justo", list_title="x" * 24)
    _fails(db_session, ClaimCategory(name="Largo", list_title="x" * 25, scope=ClaimScope.UNIT))
    _fails(db_session, ClaimCategory(name="Vacío", list_title="", scope=ClaimScope.UNIT))
    _fails(
        db_session,
        ClaimCategory(
            name="Desc", list_title="Desc", list_description="x" * 73, scope=ClaimScope.UNIT
        ),
    )


def test_scope_is_building_or_unit(db_session: Session) -> None:
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(
            text(
                "INSERT INTO claim_categories (name, list_title, scope) "
                "VALUES ('Otro', 'Otro', 'piso')"
            )
        )


def test_one_row_per_building_and_kind_of_problem(db_session: Session) -> None:
    building = f.building(db_session, "001 TORRE INVENTADA")
    category = cf.category(db_session, "Goteras inventadas")
    cf.assign(db_session, building.id, category)
    _fails(db_session, BuildingClaimCategory(building_id=building.id, category_id=category.id))


def test_whatsapp_is_unique_among_active_providers(db_session: Session) -> None:
    cf.provider(db_session, "Ascensores Ficticios SRL", WA_1)
    _fails(db_session, Provider(name="Otro Ficticio", whatsapp_e164=WA_1, active=True))
    # An inactive one may keep it, and several without WhatsApp are fine.
    cf.provider(db_session, "Viejo Ficticio", WA_1, active=False)
    cf.provider(db_session, "Sin número A")
    cf.provider(db_session, "Sin número B")
    found = provider_with_whatsapp(db_session, WA_1)
    assert found is not None and found.name == "Ascensores Ficticios SRL"
    assert provider_with_whatsapp(db_session, WA_1, exclude_id=found.id) is None


# --- The provider's WhatsApp ----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["0351 15 555-0101", "+54 9 351 555 0101", "3515550101", "351 555-0101", "5493515550101"],
)
def test_whatsapp_in_any_format(raw: str) -> None:
    assert normalize_provider_whatsapp(raw) == WA_1


def test_blank_whatsapp_is_none() -> None:
    assert normalize_provider_whatsapp("  ") is None
    assert normalize_provider_whatsapp(None) is None


@pytest.mark.parametrize("raw", ["555-0101", "hola", "12"])
def test_whatsapp_without_area_code_or_invalid(raw: str) -> None:
    with pytest.raises(SetupProblem, match="código de área"):
        normalize_provider_whatsapp(raw)


def test_warning_when_the_number_is_of_an_owner_or_a_contact(db_session: Session) -> None:
    assert whatsapp_in_use_warning(db_session, WA_1) is None
    assert whatsapp_in_use_warning(db_session, None) is None
    f.person(db_session, "Persona Inventada", phone=WA_1)
    assert "propietario o inquilino" in (whatsapp_in_use_warning(db_session, WA_1) or "")

    db_session.add(WaContact(phone_e164=WA_2, wa_id=WA_2[1:]))
    db_session.flush()
    assert "Conversaciones" in (whatsapp_in_use_warning(db_session, WA_2) or "")
    # A contact of the test chat does not count.
    db_session.add(WaContact(phone_e164=WA_3, wa_id=WA_3[1:], simulated=True))
    db_session.flush()
    assert whatsapp_in_use_warning(db_session, WA_3) is None


# --- WhatsApp's list ------------------------------------------------------------------------


def test_split_for_whatsapp() -> None:
    assert WHATSAPP_LIST_MAX_ROWS == 10
    assert split_for_whatsapp(list(range(10))) == (list(range(10)), [])
    assert split_for_whatsapp(list(range(12))) == (list(range(9)), [9, 10, 11])
    assert split_for_whatsapp([]) == ([], [])


# --- The table of a building ----------------------------------------------------------------


@pytest.fixture
def setup(db_session: Session) -> dict[str, Any]:
    building = f.building(db_session, "001 TORRE INVENTADA")
    other = f.building(db_session, "002 EDIFICIO FICTICIO")
    water = cf.category(db_session, "No hay agua inventada", list_title="Sin agua", sort_order=10)
    lift = cf.category(db_session, "Ascensor inventado", list_title="Ascensor", sort_order=20)
    old = cf.category(db_session, "Problema viejo", list_title="Viejo", active=False)
    lifts = cf.provider(db_session, "Ascensores Ficticios SRL", WA_1)
    plumber = cf.provider(db_session, "Plomería Inventada", None)
    db_session.commit()
    return {
        "building": building, "other": other, "water": water, "lift": lift, "old": old,
        "lifts": lifts, "plumber": plumber,
    }  # fmt: skip


def test_a_new_building_shows_every_active_kind_disabled(
    db_session: Session, setup: dict[str, Any]
) -> None:
    rows = building_table(db_session, setup["building"].id)
    assert [r.category.list_title for r in rows] == ["Sin agua", "Ascensor"]
    assert all(not r.enabled and r.provider is None for r in rows)
    assert [r.sort_order for r in rows] == [10, 20]


def test_save_the_whole_table(db_session: Session, setup: dict[str, Any]) -> None:
    b, water, lift, lifts = setup["building"], setup["water"], setup["lift"], setup["lifts"]
    choices = {water.id: Choice(True, None, 2), lift.id: Choice(True, lifts.id, 1)}

    change = save_building_table(db_session, b.id, choices)

    assert sorted(change.category_ids) == sorted([water.id, lift.id])
    assert change.fields == ["enabled", "provider_id", "sort_order"]
    assert _rows(db_session, b.id) == {water.id: (True, None, 2), lift.id: (True, lifts.id, 1)}
    # In the building's order.
    titles = [r.category.list_title for r in building_table(db_session, b.id)]
    assert titles == ["Ascensor", "Sin agua"]
    # Saved again unchanged: nothing changed (the panel logs nothing).
    assert save_building_table(db_session, b.id, choices).category_ids == []


def test_save_leaves_inactive_kinds_alone(db_session: Session, setup: dict[str, Any]) -> None:
    b, old, lifts = setup["building"], setup["old"], setup["lifts"]
    cf.assign(db_session, b.id, old, lifts)
    save_building_table(db_session, b.id, {old.id: Choice(False, None, 0)})
    assert _rows(db_session, b.id)[old.id] == (True, lifts.id, old.sort_order)


def test_save_refuses_an_inactive_provider_unless_it_was_already_there(
    db_session: Session, setup: dict[str, Any]
) -> None:
    b, lift, lifts = setup["building"], setup["lift"], setup["lifts"]
    lifts.active = False
    db_session.commit()
    with pytest.raises(SetupProblem, match="proveedor activo"):
        save_building_table(db_session, b.id, {lift.id: Choice(True, lifts.id, 1)})
    db_session.rollback()

    cf.assign(db_session, b.id, lift, lifts)
    db_session.commit()
    save_building_table(db_session, b.id, {lift.id: Choice(True, lifts.id, 3)})
    assert _rows(db_session, b.id)[lift.id] == (True, lifts.id, 3)


def test_save_refuses_a_negative_order(db_session: Session, setup: dict[str, Any]) -> None:
    choices = {setup["water"].id: Choice(True, None, -1)}
    with pytest.raises(SetupProblem, match="orden"):
        save_building_table(db_session, setup["building"].id, choices)


def test_copy_from_another_building(db_session: Session, setup: dict[str, Any]) -> None:
    b, other, water, lift = setup["building"], setup["other"], setup["water"], setup["lift"]
    lifts = setup["lifts"]
    cf.assign(db_session, other.id, lift, lifts, sort_order=1)  # the source has no water row
    cf.assign(db_session, b.id, water, setup["plumber"], sort_order=7)
    db_session.commit()

    change = copy_building_table(db_session, b.id, other.id)

    assert sorted(change.category_ids) == sorted([water.id, lift.id])
    assert _rows(db_session, b.id) == {
        water.id: (False, None, water.sort_order),  # the source never had it: disabled
        lift.id: (True, lifts.id, 1),
    }
    assert _rows(db_session, other.id) == {lift.id: (True, lifts.id, 1)}  # untouched


def test_copy_from_itself_is_refused(db_session: Session, setup: dict[str, Any]) -> None:
    with pytest.raises(SetupProblem, match="otro edificio"):
        copy_building_table(db_session, setup["building"].id, setup["building"].id)


def test_warnings(db_session: Session, setup: dict[str, Any]) -> None:
    b, water, lift, plumber = setup["building"], setup["water"], setup["lift"], setup["plumber"]
    assert table_warnings(building_table(db_session, b.id)) == []  # nothing enabled

    cf.assign(db_session, b.id, water)  # the studio
    cf.assign(db_session, b.id, lift, plumber)  # no WhatsApp
    assert table_warnings(building_table(db_session, b.id)) == [
        "Estos los va a atender el estudio (no tienen proveedor): Sin agua.",
        "Plomería Inventada no tiene WhatsApp cargado: el bot no le va a poder avisar (Ascensor).",
    ]

    plumber.active = False
    db_session.flush()
    warnings = table_warnings(building_table(db_session, b.id))
    assert "Plomería Inventada está desactivado: elegí otro proveedor para Ascensor." in warnings
