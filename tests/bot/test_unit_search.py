"""Tolerant unit search against the Postgres test database. All data here is invented.

Unit labels mimic the shapes ConsorPlus uses ("04-C", "4º D", "PB-A", "COC-12", "TII4A").
"""

import pytest
from sqlalchemy.orm import Session

from app.bot.unit_search import SearchStatus, search_unit, tokens
from tests.bot import factories as f


@pytest.fixture
def units(db_session: Session) -> dict[str, int]:
    rodas2 = f.building(db_session, "045 RODAS II")
    rodas1 = f.building(db_session, "046 EDIFICIO RODAS I")
    sol = f.building(db_session, "050 TORRE DEL SOL")
    closed = f.building(db_session, "051 RODAS III", active=False)
    towers = f.building(db_session, "052 JARDINES DEL ESTE")
    return {
        "rodas2_4c": f.unit(db_session, rodas2, "04-C").id,
        "rodas2_4d": f.unit(db_session, rodas2, "4º D").id,
        "rodas2_1b": f.unit(db_session, rodas2, "01-B").id,
        "rodas2_pba": f.unit(db_session, rodas2, "PB-A").id,
        "rodas2_coc12": f.unit(db_session, rodas2, "COC-12").id,
        "rodas2_pbloc": f.unit(db_session, rodas2, "PB-LOC").id,
        "rodas2_old": f.unit(db_session, rodas2, "09-Z", active=False).id,
        "rodas1_4c": f.unit(db_session, rodas1, "4° C").id,
        "rodas1_2b": f.unit(db_session, rodas1, "02 B").id,
        "rodas1_2a": f.unit(db_session, rodas1, "02 A").id,
        "sol_pba": f.unit(db_session, sol, "PB A").id,
        "sol_loca": f.unit(db_session, sol, "LOC-A").id,
        "sol_locb": f.unit(db_session, sol, "LOC-B").id,
        "closed_1a": f.unit(db_session, closed, "01-A").id,
        "t1_4a": f.unit(db_session, towers, "TI4A").id,
        "t4_1a": f.unit(db_session, towers, "TIV-1A").id,
    }


def test_tokens_normalization() -> None:
    assert tokens("Rodas II", roman=True) == ["rodas", "2"]
    assert tokens("4º C") == ["4", "c"]
    assert tokens('Piso 04, Dpto. "C"') == ["4", "c"]
    assert tokens("4to C") == ["4", "c"]
    assert tokens("Planta Baja A") == ["pb", "a"]
    assert tokens("PBA") == ["pb", "a"]
    assert tokens("Cochera 12") == tokens("COC-12") == tokens("CH.12") == ["coc", "12"]
    assert tokens("TII4A") == tokens("torre 2 4A") == ["t", "2", "4", "a"]
    assert tokens("TIVPBA") == ["t", "4", "pb", "a"]


@pytest.mark.parametrize(
    ("building_text", "unit_text", "expected"),
    [
        ("Rodas 2", "4C", "rodas2_4c"),
        ("rodas II", "4º C", "rodas2_4c"),
        ("RODAS  ii", "piso 4 dpto c", "rodas2_4c"),
        ("Rodas 2 4C", "", "rodas2_4c"),
        ("4 C de Rodas II", "", "rodas2_4c"),
        ("rodas II 4º C", "", "rodas2_4c"),
        ("Rodaz 2", "4 C", "rodas2_4c"),  # typo in the building
        ("Rodas 2", "cochera 12", "rodas2_coc12"),
        ("Rodas II", "planta baja A", "rodas2_pba"),
        # "1" in the unit must not make "Rodas I" a candidate.
        ("piso 1 depto b de rodas II", "", "rodas2_1b"),
        ("Jardines", "torre 1 4A", "t1_4a"),
        ("Jardines", "TIV 1A", "t4_1a"),  # same tokens as TI-4A, different order
        ("Torre del Sol", "PB A", "sol_pba"),
        # Locales and cocheras: any order.
        ("Rodas II", "local de planta baja", "rodas2_pbloc"),
        ("Rodas II", "planta baja local", "rodas2_pbloc"),
        ("local de planta baja del Rodas II", "", "rodas2_pbloc"),
        ("Rodas II", "12 cochera", "rodas2_coc12"),
        ("Torre del Sol", "local a", "sol_loca"),
        ("Torre del Sol", "A local", "sol_loca"),
    ],
)
def test_finds_unit_in_many_spellings(
    db_session: Session, units: dict[str, int], building_text: str, unit_text: str, expected: str
) -> None:
    result = search_unit(db_session, building_text, unit_text)

    assert result.status == SearchStatus.FOUND
    assert result.unit is not None
    assert result.unit.unit_id == units[expected]


def test_same_unit_in_two_similar_buildings_is_ambiguous(
    db_session: Session, units: dict[str, int]
) -> None:
    result = search_unit(db_session, "Rodas", "4C")

    assert result.status == SearchStatus.AMBIGUOUS
    assert result.unit is None
    assert {c.unit_id for c in result.candidates} == {units["rodas2_4c"], units["rodas1_4c"]}
    assert len(result.buildings) == 2


def test_does_not_pick_alone_when_building_is_unclear(
    db_session: Session, units: dict[str, int]
) -> None:
    # Only Rodas I has a 2B, but "Rodas" alone matches both buildings: ask, do not guess.
    result = search_unit(db_session, "Rodas", "2B")

    assert result.status == SearchStatus.AMBIGUOUS
    assert [c.unit_id for c in result.candidates] == [units["rodas1_2b"]]


def test_close_unit_is_offered_not_chosen(db_session: Session, units: dict[str, int]) -> None:
    result = search_unit(db_session, "Rodas I", "2")

    assert result.status == SearchStatus.AMBIGUOUS
    assert {c.unit_id for c in result.candidates} == {units["rodas1_2a"], units["rodas1_2b"]}


@pytest.mark.parametrize(
    ("building_text", "unit_text"),
    [
        ("Rodas II", "7F"),  # no such unit
        ("Edificio Inexistente", "4C"),
        ("Rodas II", "9Z"),  # inactive unit
        ("Rodas III", "1A"),  # inactive building
        ("", "4C"),
    ],
)
def test_not_found(
    db_session: Session, units: dict[str, int], building_text: str, unit_text: str
) -> None:
    result = search_unit(db_session, building_text, unit_text)

    assert result.status != SearchStatus.FOUND
    assert all(
        c.unit_id not in (units["rodas2_old"], units["closed_1a"]) for c in result.candidates
    )


def test_incomplete_local_is_offered_not_chosen(db_session: Session, units: dict[str, int]) -> None:
    result = search_unit(db_session, "Torre del Sol", "el local")

    assert result.status == SearchStatus.AMBIGUOUS
    assert {c.unit_id for c in result.candidates} == {units["sol_loca"], units["sol_locb"]}


def test_departments_keep_the_order(db_session: Session, units: dict[str, int]) -> None:
    # "A PB" is not read as "PB A": only locales and cocheras are order-free.
    result = search_unit(db_session, "Torre del Sol", "A PB")

    assert result.status != SearchStatus.FOUND
