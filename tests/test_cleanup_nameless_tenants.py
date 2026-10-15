"""scripts/cleanup_nameless_tenants.py against the test database. All data is invented."""

import importlib.util
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import EVENT_TYPE
from app.db.models import (
    BotEvent,
    DataSource,
    Person,
    PersonRole,
    Phone,
    UnitPerson,
    VerificationRequest,
)
from app.sync.roster import NAMELESS_PERSON
from tests.bot import factories as f

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "cleanup_nameless_tenants.py"
_spec = importlib.util.spec_from_file_location("cleanup_nameless_tenants", _PATH)
cleanup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cleanup)


def _nameless(session: Session, *, full_name: str = NAMELESS_PERSON) -> Person:
    person = Person(full_name=full_name, consorplus_name=NAMELESS_PERSON, email="x@example.com")
    session.add(person)
    session.flush()
    return person


def _link(session: Session, unit, person, role=PersonRole.TENANT, source=DataSource.CONSORPLUS):
    session.add(UnitPerson(unit_id=unit.id, person_id=person.id, role=role, source=source))
    session.flush()


def test_only_the_nameless_consorplus_tenants_without_phones_go(db_session: Session) -> None:
    building = f.building(db_session, "EDIFICIO INVENTADO")
    unit, other = f.unit(db_session, building, "1-A"), f.unit(db_session, building, "2-B")

    gone = _nameless(db_session)  # tenant of two units, from ConsorPlus, no phones
    _link(db_session, unit, gone)
    _link(db_session, other, gone)
    with_phone = _nameless(db_session)
    _link(db_session, unit, with_phone)
    db_session.add(Phone(person_id=with_phone.id, e164="+5493515550601", source=DataSource.MANUAL))
    also_owner = _nameless(db_session)
    _link(db_session, unit, also_owner)
    _link(db_session, other, also_owner, PersonRole.OWNER)
    by_hand = _nameless(db_session)
    _link(db_session, unit, by_hand, source=DataSource.MANUAL)
    renamed = _nameless(db_session, full_name="Nombre puesto en el panel")
    _link(db_session, unit, renamed)
    named = f.person(db_session, "INQUILINO CON NOMBRE")
    _link(db_session, unit, named)
    no_units = _nameless(db_session)
    db_session.add(
        VerificationRequest(phone_e164="+5493515550699", unit_id=unit.id, claimed_name="X",
                            person_id=gone.id)
    )  # fmt: skip
    db_session.commit()

    assert cleanup.nameless_tenants(db_session) == [gone.id]

    assert cleanup.delete_nameless_tenants(db_session) == 1

    db_session.expire_all()
    left = set(db_session.scalars(select(Person.id)))
    assert gone.id not in left
    assert {with_phone.id, also_owner.id, by_hand.id, renamed.id, named.id, no_units.id} <= left
    assert db_session.scalar(select(VerificationRequest.person_id)) is None  # kept, unlinked
    [event] = db_session.scalars(select(BotEvent).where(BotEvent.event_type == EVENT_TYPE))
    assert event.payload == {
        "admin_user": cleanup.AUDIT_USER, "action": "nameless_tenants_deleted", "people": 1,
    }  # fmt: skip
    assert cleanup.nameless_tenants(db_session) == []
    assert cleanup.delete_nameless_tenants(db_session) == 0  # again: nothing, no event
