"""Invented buildings, units, people and phones for the bot tests."""

from itertools import count

from sqlalchemy.orm import Session

from app.db.models import Building, DataSource, Person, PersonRole, Phone, Unit, UnitPerson

_codes = count(9000)


def building(session: Session, name: str, *, active: bool = True) -> Building:
    b = Building(consorplus_code=next(_codes), name=name, active=active)
    session.add(b)
    session.flush()
    return b


def unit(session: Session, b: Building, label: str, *, active: bool = True) -> Unit:
    u = Unit(building=b, consorplus_unit_value=label, label=label, active=active)
    session.add(u)
    session.flush()
    return u


def person(
    session: Session,
    name: str,
    *,
    email: str | None = None,
    phone: str | None = None,
    needs_review: bool = False,
    conflict: bool = False,
) -> Person:
    p = Person(full_name=name, email=email)
    session.add(p)
    session.flush()
    if phone:
        session.add(
            Phone(
                person_id=p.id,
                e164=phone,
                source=DataSource.CONSORPLUS,
                needs_review=needs_review,
                conflict=conflict,
            )
        )
        session.flush()
    return p


def link(session: Session, u: Unit, p: Person, role: PersonRole = PersonRole.OWNER) -> None:
    session.add(UnitPerson(unit_id=u.id, person_id=p.id, role=role, source=DataSource.CONSORPLUS))
    session.flush()
