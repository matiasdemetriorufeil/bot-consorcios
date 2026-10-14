"""Data steps of migrations, run against the Postgres test database. Invented data only."""

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import Building, DataSource, Person, PersonRole, Phone, Unit, UnitPerson

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _migration(name: str) -> ModuleType:
    path = next(VERSIONS.glob(f"*_{name}.py"))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_phones_conflict_backfill_splits_conflicts_from_assumed_area_codes(
    db_session: Session,
) -> None:
    person = Person(full_name="PERSONA INVENTADA")
    db_session.add(person)
    db_session.flush()
    rows = {
        "+5493515550101": ("0351 15 555-0101", True),  # full number: was a conflict
        "+5493515550202": ("5550202", True),  # area code assumed
        "+5493515550303": ("15 5550303", True),  # area code assumed (mobile)
        "+5493515550404": ("3515550404", False),  # nothing flagged
    }
    for e164, (raw, flagged) in rows.items():
        db_session.add(
            Phone(
                person_id=person.id,
                e164=e164,
                raw=raw,
                source=DataSource.CONSORPLUS,
                needs_review=flagged,
            )
        )
    db_session.flush()

    db_session.execute(text(_migration("phones_conflict")._MOVE_CONFLICTS))
    db_session.expire_all()

    result = {p.e164: (p.needs_review, p.conflict) for p in db_session.scalars(select(Phone))}
    assert result == {
        "+5493515550101": (False, True),
        "+5493515550202": (True, False),
        "+5493515550303": (True, False),
        "+5493515550404": (False, False),
    }


def test_consorplus_name_backfill_only_for_people_linked_from_consorplus(
    db_session: Session,
) -> None:
    building = Building(consorplus_code=9901, name="EDIFICIO INVENTADO")
    db_session.add(building)
    db_session.flush()
    unit = Unit(building_id=building.id, consorplus_unit_value="1", label="1-A")
    from_cp = Person(full_name="PERSONA DE CONSORPLUS")
    by_hand = Person(full_name="Persona a mano")
    db_session.add_all([unit, from_cp, by_hand])
    db_session.flush()
    db_session.add_all(
        [
            UnitPerson(
                unit_id=unit.id, person_id=from_cp.id, role=PersonRole.OWNER,
                source=DataSource.CONSORPLUS,
            ),
            UnitPerson(
                unit_id=unit.id, person_id=by_hand.id, role=PersonRole.RESIDENT,
                source=DataSource.MANUAL,
            ),
        ]
    )  # fmt: skip
    db_session.flush()

    db_session.execute(text(_migration("neighbors_roster")._BACKFILL_CONSORPLUS_NAME))
    db_session.expire_all()

    assert db_session.get(Person, from_cp.id).consorplus_name == "PERSONA DE CONSORPLUS"
    assert db_session.get(Person, by_hand.id).consorplus_name is None
