"""The "Vecinos" page: one building's roster, a search in all of them, filters, the counter and,
for admins only, «Actualizar desde ConsorPlus» and «Quitados a mano». Invented data only."""

import html
import re

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.admin import neighbors, neighbors_service
from app.admin.formatting import phone
from app.admin.help import EMPTY, PAGE_HELP
from app.db.models import (
    Building,
    DataSource,
    Person,
    PersonRole,
    Phone,
    RosterSuppression,
    Unit,
    UnitPerson,
)
from app.sync.nightly import advisory_lock
from tests.admin.conftest import ADMIN, Panel
from tests.sync.test_roster_sync import ANA, FakeSource, row

ANA_PHONE = "+5493515550101"
RITA_PHONE = "+5493515550102"
CARLOS_PHONE = "+5493515550201"  # to review
DORA_PHONE = "+5493515550301"  # in conflict
ELENA_PHONE = "+5493515550501"
FETCH = {"X-Panel-Fetch": "1", "Accept": "application/json"}


def _unit(s: Session, building: Building, value: str, label: str) -> Unit:
    unit = Unit(building_id=building.id, consorplus_unit_value=value, label=label)
    s.add(unit)
    s.flush()
    return unit


def _person(s: Session, name: str, *phones: tuple[str, DataSource, dict]) -> Person:
    person = Person(full_name=name, consorplus_name=name)
    s.add(person)
    s.flush()
    for e164, source, flags in phones:
        s.add(Phone(person_id=person.id, e164=e164, source=source, **flags))
    s.flush()
    return person


def _link(s: Session, unit: Unit, person: Person, role: PersonRole, source=DataSource.CONSORPLUS):
    s.add(UnitPerson(unit_id=unit.id, person_id=person.id, role=role, source=source))
    s.flush()


@pytest.fixture
def roster(db_session: Session) -> dict[str, int]:
    """TORRE VECINOS: 4 units listed by ConsorPlus as 1, 2, 3, 10 (labels that do not sort
    alone); SOL VECINOS: one more «PB A»."""
    s = db_session
    torre = Building(consorplus_code=45, name="045 TORRE VECINOS")
    sol = Building(consorplus_code=50, name="050 SOL VECINOS")
    s.add_all([torre, sol])
    s.flush()
    pb = _unit(s, torre, "1", "PB A")
    first = _unit(s, torre, "2", "1° B")
    second = _unit(s, torre, "3", "2° A")
    tenth = _unit(s, torre, "10", "10° C")
    _unit(s, torre, "11", "BAJA").active = False
    sol_pb = _unit(s, sol, "5", "PB A")
    cp = DataSource.CONSORPLUS
    ana = _person(s, "Ana Inventada", (ANA_PHONE, cp, {}))
    bruno = _person(s, "Bruno Inquilino")
    rita = _person(s, "Rita Familiar", (RITA_PHONE, DataSource.MANUAL, {"verified": True}))
    carlos = _person(s, "Carlos Revisar", (CARLOS_PHONE, cp, {"needs_review": True}))
    dora = _person(s, "Dora Conflicto", (DORA_PHONE, cp, {"conflict": True}))
    elena = _person(s, "Elena Sol", (ELENA_PHONE, DataSource.BOT_VERIFIED, {"verified": True}))
    _link(s, pb, ana, PersonRole.OWNER)
    _link(s, pb, bruno, PersonRole.TENANT)
    _link(s, pb, rita, PersonRole.RESIDENT, DataSource.MANUAL)
    _link(s, first, carlos, PersonRole.OWNER)
    _link(s, second, dora, PersonRole.OWNER)
    _link(s, sol_pb, elena, PersonRole.OWNER, DataSource.BOT_VERIFIED)
    s.commit()
    return {"torre": torre.id, "sol": sol.id, "pb": pb.id, "tenth": tenth.id, "ana": ana.id}


def _get(panel: Panel, **params) -> str:
    response = panel.client.get("/admin/neighbors", params=params)
    assert response.status_code == 200
    return html.unescape(response.text)


def _units(page: str) -> list[str]:
    return re.findall(r'<th scope="rowgroup" class="neighbor-unit-label"[^>]*>([^<]+)</th>', page)


def _results(page: str) -> str:
    return page.split('id="neighbors-results"', 1)[1]


# --- What everyone sees ----------------------------------------------------------------------


def test_one_building_in_the_order_of_consorplus(operator: Panel, roster) -> None:
    page = _get(operator, building_id=roster["torre"])

    assert PAGE_HELP["view-neighbors"] in page
    assert _units(page) == ["PB A", "1° B", "2° A", "10° C"]  # 1, 2, 3, 10; not the inactive
    results = _results(page)
    assert "2 de 4 unidades con teléfono del propietario" in re.sub(r"</?strong>", "", results)
    for text in ("Propietario/a", "Inquilino/a", "Familiar u otro", "ConsorPlus", "Cargado a mano"):
        assert text in results
    assert phone(ANA_PHONE) in results and phone(RITA_PHONE) in results
    assert "Sin teléfono" in results  # Bruno
    assert "Sin datos" in results  # 10° C
    assert "Elena Sol" not in results  # another building
    assert "4 unidades" in results


def test_phones_that_do_not_identify_carry_their_state_and_a_link(operator: Panel, roster) -> None:
    results = _results(_get(operator, building_id=roster["torre"]))

    review = re.search(r'<a class="state state-[a-z]+" href="([^"]+)">A revisar</a>', results)
    conflict = re.search(r'<a class="state state-[a-z]+" href="([^"]+)">En conflicto</a>', results)
    assert review and conflict
    assert review.group(1).endswith("/admin/phones?tab=all&q=3515550201")
    assert conflict.group(1).endswith("/admin/phones?tab=all&q=3515550301")
    assert results.count('class="state ') == 2  # the others are fine


def test_the_building_is_chosen_by_its_name(operator: Panel, roster) -> None:
    page = _get(operator, building="torre vecinos")
    assert _units(page) == ["PB A", "1° B", "2° A", "10° C"]
    assert '<option value="TORRE VECINOS">' in page and '<option value="SOL VECINOS">' in page

    unknown = _get(operator, building="no existe")
    assert "No encontramos ese edificio" in unknown
    assert EMPTY["neighbors:choose"] in unknown


def test_two_buildings_with_the_same_name_are_never_guessed(
    operator: Panel, roster, db_session: Session
) -> None:
    db_session.add(Building(consorplus_code=60, name="060 TORRE VECINOS"))
    db_session.commit()

    page = _get(operator, building="TORRE VECINOS")

    assert "Hay más de un edificio con ese nombre" in page
    assert '<option value="TORRE VECINOS (código 45)">' in page
    assert '<option value="TORRE VECINOS (código 60)">' in page
    assert _units(page) == []
    assert _units(_get(operator, building="torre vecinos (código 45)")) == [
        "PB A", "1° B", "2° A", "10° C",
    ]  # fmt: skip


def test_without_a_building_nor_a_search(operator: Panel, roster) -> None:
    page = _get(operator)
    assert EMPTY["neighbors:choose"] in page and _units(page) == []


# --- Search in all the buildings -------------------------------------------------------------


def test_search_by_name(operator: Panel, roster) -> None:
    results = _results(_get(operator, q="elena"))
    assert "Elena Sol" in results and "SOL VECINOS" in results and "<th>Edificio</th>" in results
    assert "Ana Inventada" not in results
    assert "unidades con teléfono" not in results  # the counter is per building


def test_search_by_unit_finds_it_in_every_building(operator: Panel, roster) -> None:
    results = _results(_get(operator, q="PB A", building_id=roster["torre"]))
    assert _units(results) == ["PB A", "PB A"]
    assert "SOL VECINOS" in results and "TORRE VECINOS" in results
    assert "Ana Inventada" in results and "Rita Familiar" in results and "Elena Sol" in results

    empty = _results(_get(operator, q="10° C"))
    assert _units(empty) == ["10° C"] and "Sin datos" in empty


@pytest.mark.parametrize(
    "number", ["351 555-0101", "0351 15 555-0101", "+54 9 351 5550101", "5550101", "3515550101"]
)
def test_search_by_phone_in_any_format(operator: Panel, roster, number: str) -> None:
    results = _results(_get(operator, q=number))
    assert "Ana Inventada" in results
    assert "Elena Sol" not in results and "Bruno Inquilino" not in results


def test_search_without_results(operator: Panel, roster) -> None:
    assert EMPTY["neighbors:search"] in _get(operator, q="nadie se llama así")


# --- Filters and pages -----------------------------------------------------------------------


def test_filter_without_owner_phone(operator: Panel, roster) -> None:
    page = _get(operator, building_id=roster["torre"], only="no_phone")
    assert _units(page) == ["2° A", "10° C"]  # in conflict, and nobody
    assert 'aria-current="true">Sin teléfono</a>' in page
    assert "2 de 4 unidades" in re.sub(r"</?strong>", "", page)  # the counter stays whole


def test_filter_loaded_by_hand(operator: Panel, roster) -> None:
    results = _results(_get(operator, building_id=roster["torre"], only="manual"))
    assert _units(results) == ["PB A"]
    assert "Rita Familiar" in results and "Ana Inventada" not in results


def test_pages_never_split_a_unit(operator: Panel, roster, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(neighbors, "PAGE_SIZE", 3)
    first = _get(operator, building_id=roster["torre"])
    second = _get(operator, building_id=roster["torre"], page="2")
    assert _units(first) == ["PB A", "1° B", "2° A"] and _units(second) == ["10° C"]
    assert "Rita Familiar" in first and 'aria-label="Página siguiente"' in first


# --- Admins only -----------------------------------------------------------------------------


def _suppress_ana_phone(db_session: Session) -> int:
    ana_phone = db_session.scalar(select(Phone).where(Phone.e164 == ANA_PHONE))
    assert neighbors_service.remove_phone(db_session, ADMIN, ana_phone.id)
    return db_session.scalar(select(RosterSuppression.id))


def test_the_operator_neither_sees_nor_calls_the_admin_part(
    operator: Panel, roster, db_session: Session
) -> None:
    suppression = _suppress_ana_phone(db_session)
    page = _get(operator, building_id=roster["torre"])
    assert "Actualizar desde ConsorPlus" not in page and "Quitados a mano" not in page

    for url, data in (
        ("/admin/neighbors/refresh", {"building_id": roster["torre"]}),
        (f"/admin/neighbors/suppressions/{suppression}/restore", {}),
    ):
        plain = operator.client.post(url, data=data, follow_redirects=False)
        assert plain.status_code == 403
        fetched = operator.client.post(url, data=data, headers=FETCH)
        assert fetched.status_code == 403
        assert fetched.json()["message"] == "Esta acción es solo para administradores."
    db_session.expire_all()
    assert db_session.get(RosterSuppression, suppression) is not None
    assert operator.admin_events("roster_refreshed") == []


def test_without_login_it_goes_to_the_login(panel: Panel) -> None:
    response = panel.client.get("/admin/neighbors", follow_redirects=False)
    assert response.status_code in (302, 303) and "/admin/login" in response.headers["location"]


def test_the_admin_sees_what_was_removed_and_restores_it(
    logged_in: Panel, roster, db_session: Session
) -> None:
    suppression = _suppress_ana_phone(db_session)

    page = _get(logged_in, building_id=roster["torre"])
    assert "Actualizar desde ConsorPlus" in page
    assert re.search(r"Quitados a mano \(<span data-removed-count>1</span>\)", page)
    assert f"Teléfono {phone(ANA_PHONE)} · Ana Inventada" in page
    assert "Quitados a mano" not in _get(logged_in, q="ana")  # only with a building

    reply = logged_in.client.post(
        f"/admin/neighbors/suppressions/{suppression}/restore",
        data={"back": f"building_id={roster['torre']}"},
        headers=FETCH,
    ).json()

    assert reply["ok"] is True and "Restaurado" in reply["message"]
    assert reply["remove"] == [f'[data-suppression="{suppression}"]']
    assert reply["texts"] == {"[data-removed-count]": "0"}
    db_session.expire_all()
    assert db_session.get(RosterSuppression, suppression) is None
    [event] = logged_in.admin_events("roster_suppression_restored")
    assert event["suppression_id"] == suppression
    assert EMPTY["neighbors:removed"] in _get(logged_in, building_id=roster["torre"])


def test_refresh_with_the_nightly_sync_running(
    logged_in: Panel, roster, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(neighbors.NeighborsView, "roster_source", FakeSource({"45": []}))
    monkeypatch.setattr(neighbors.NeighborsView, "lock_engine", db_engine)

    with advisory_lock(db_engine):
        reply = logged_in.client.post(
            "/admin/neighbors/refresh", data={"building_id": roster["torre"]}, headers=FETCH
        ).json()

    assert reply["ok"] is False
    assert reply["message"] == "Ya se está sincronizando, probá en unos minutos."
    assert logged_in.admin_events("roster_refreshed") == []


def test_refresh_reads_the_building_and_answers_counters_only(
    logged_in: Panel, roster, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = FakeSource({"45": [row("1", ANA, building="045")]})
    monkeypatch.setattr(neighbors.NeighborsView, "roster_source", source)
    monkeypatch.setattr(neighbors.NeighborsView, "lock_engine", db_engine)

    reply = logged_in.client.post(
        "/admin/neighbors/refresh",
        data={"building_id": roster["torre"], "back": f"building_id={roster['torre']}"},
        headers=FETCH,
    ).json()

    assert reply["ok"] is True
    assert reply["message"].startswith("Listo: se leyeron 1 unidades.")
    assert "LOPEZ" not in reply["message"] and "Ana" not in reply["message"]
    assert "#neighbors-results" in reply["replace"]
    [event] = logged_in.admin_events("roster_refreshed")
    assert event["building_id"] == roster["torre"] and event["status"] == "ok"


def test_refresh_when_consorplus_fails(
    logged_in: Panel, roster, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.consorplus.errors import ConsorPlusUnavailableError

    source = FakeSource({"45": ConsorPlusUnavailableError("caído (inventado)")})
    monkeypatch.setattr(neighbors.NeighborsView, "roster_source", source)
    monkeypatch.setattr(neighbors.NeighborsView, "lock_engine", db_engine)

    reply = logged_in.client.post(
        "/admin/neighbors/refresh", data={"building_id": roster["torre"]}, headers=FETCH
    ).json()

    assert reply["ok"] is False and reply["message"] == neighbors.REFRESH_FAILED
    assert reply["replace"] == {}
