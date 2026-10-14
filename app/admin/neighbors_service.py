"""The roster edited by hand ("Vecinos"): what the panel's page calls. No HTTP here.

Each function is one transaction (it commits) and leaves its trace in bot_events
(log_admin_action: ids, roles and sources, never names). Errors the operator must read are
NeighborError, in Spanish.

What came from ConsorPlus and is removed or changed here leaves a roster_suppressions row, so
the roster sync (app.sync.roster) does not bring it back at night. A link or phone loaded by hand
(or verified by the bot) is changed in place: the sync never touches those.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.admin import formatting
from app.admin.audit import log_admin_action
from app.config import get_settings
from app.consorplus import ConsorPlusClient
from app.db.models import (
    Building,
    DataSource,
    Person,
    PersonRole,
    Phone,
    Provider,
    RosterSuppression,
    SuppressionKind,
    SyncKind,
    Unit,
    UnitPerson,
)
from app.db.session import engine as default_engine
from app.person_roles import role_text
from app.sync.nightly import AlreadyRunningError, advisory_lock
from app.sync.normalize import NormalizedPhone, clean_name, normalize_phone
from app.sync.roster import RosterSource, consorplus_key, sync_roster

MAX_NAME = 200  # people.full_name


class NeighborError(Exception):
    """Something the operator asked that cannot be done; the message says why (Spanish)."""


@dataclass(frozen=True)
class PhoneTaken:
    """The number is already another person's: nothing was changed (move_phone moves it)."""

    phone_id: int
    e164: str
    person_id: int
    full_name: str
    units: list[str]


@dataclass(frozen=True)
class PersonSaved:
    person_id: int
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PhoneSaved:
    phone_id: int
    warning: str | None = None


# --- Checks ----------------------------------------------------------------------------------


def _full_name(name: str | None) -> str:
    cleaned = clean_name(name or "")
    if not cleaned:
        raise NeighborError("Escribí el nombre y apellido.")
    if len(cleaned) > MAX_NAME:
        raise NeighborError(f"El nombre es muy largo (hasta {MAX_NAME} letras).")
    return cleaned


def _person(session: Session, person_id: int) -> Person:
    person = session.get(Person, person_id, with_for_update=True)
    if person is None:
        raise NeighborError("Esa persona ya no está en el padrón.")
    return person


def _active_unit(session: Session, unit_id: int) -> Unit:
    unit = session.get(Unit, unit_id)
    if unit is None or not unit.active:
        raise NeighborError("La unidad no existe o está dada de baja.")
    return unit


def _link(session: Session, unit_id: int, person_id: int, role: PersonRole) -> UnitPerson:
    link = session.get(UnitPerson, (unit_id, person_id, role), with_for_update=True)
    if link is None:
        raise NeighborError("Esa persona ya no está en esa unidad con ese rol.")
    return link


def _parse_phone(raw: str) -> NormalizedPhone:
    phone = normalize_phone(raw or "")
    if phone is None:
        raise NeighborError("Ese número no es válido. Escribilo con la característica, ej. 351…")
    if phone.needs_review:
        raise NeighborError(
            "A ese número le falta la característica (por ejemplo 351): escribila para no "
            "tener que adivinarla."
        )
    return phone


def units_text(person: Person) -> list[str]:
    """ "Edificio · unidad (rol)" of each unit of the person."""
    links = sorted(
        person.units, key=lambda link: (link.unit.building.name, link.unit.label, link.role)
    )
    return [
        f"{formatting.building(link.unit.building.name)} · {link.unit.label}"
        f" ({role_text(link.role)})"
        for link in links
    ]


def _taken(session: Session, e164: str, person_id: int | None) -> PhoneTaken | None:
    """Another person's number (None if it is free). Already this person's: an error."""
    phone = session.scalar(select(Phone).where(Phone.e164 == e164))
    if phone is None:
        return None
    if phone.person_id == person_id:
        raise NeighborError("Esa persona ya tiene ese número.")
    owner = phone.person
    return PhoneTaken(phone.id, phone.e164, owner.id, owner.full_name, units_text(owner))


def provider_warning(session: Session, e164: str) -> str | None:
    """A warning (it does not block) when the number is an active provider's WhatsApp: its
    messages go to the providers' flow, not to the neighbors' one."""
    name = session.scalar(
        select(Provider.name)
        .where(Provider.whatsapp_e164 == e164, Provider.active.is_(True))
        .limit(1)
    )
    if name is None:
        return None
    return (
        f"Ojo: ese número también es el WhatsApp del proveedor «{name}». Sus mensajes se van a "
        "tomar como de un proveedor."
    )


# --- Suppressions ----------------------------------------------------------------------------


def _suppress_link(session: Session, user: str, link: UnitPerson) -> None:
    key = consorplus_key(link.person)
    exists = session.scalar(
        select(RosterSuppression.id).where(
            RosterSuppression.kind == SuppressionKind.LINK,
            RosterSuppression.unit_id == link.unit_id,
            RosterSuppression.role == link.role,
            RosterSuppression.name_key == key,
        )
    )
    if exists is None:
        session.add(
            RosterSuppression(
                kind=SuppressionKind.LINK,
                unit_id=link.unit_id,
                role=link.role,
                name_key=key,
                building_id=link.unit.building_id,
                person_name=link.person.full_name,
                created_by=user,
            )
        )


def _building_of(person: Person) -> int | None:
    """The building a phone removal is listed in («Quitados a mano»): the one of the person's
    first unit."""
    links = sorted(person.units, key=lambda link: link.unit_id)
    return links[0].unit.building_id if links else None


def _suppress_phone(
    session: Session, user: str, phone: Phone, building_id: int | None = None
) -> None:
    key = consorplus_key(phone.person)
    exists = session.scalar(
        select(RosterSuppression.id).where(
            RosterSuppression.kind == SuppressionKind.PHONE,
            RosterSuppression.e164 == phone.e164,
            RosterSuppression.name_key == key,
        )
    )
    if exists is None:
        session.add(
            RosterSuppression(
                kind=SuppressionKind.PHONE,
                e164=phone.e164,
                name_key=key,
                building_id=building_id or _building_of(phone.person),
                person_name=phone.person.full_name,
                created_by=user,
            )
        )


def restore_suppression(session: Session, user: str, suppression_id: int) -> bool:
    """Forget a removal: the next sync of the building brings that data back."""
    suppression = session.get(RosterSuppression, suppression_id, with_for_update=True)
    if suppression is None:
        return False
    log_admin_action(
        session,
        user,
        "roster_suppression_restored",
        phone_e164=suppression.e164,
        suppression_id=suppression.id,
        kind=str(suppression.kind),
        unit_id=suppression.unit_id,
        role=str(suppression.role) if suppression.role else None,
    )
    session.delete(suppression)
    session.commit()
    return True


# --- People and their units ------------------------------------------------------------------


def add_person(
    session: Session,
    user: str,
    unit_id: int,
    role: PersonRole,
    *,
    full_name: str | None = None,
    person_id: int | None = None,
    phones: Iterable[str] = (),
) -> PersonSaved | PhoneTaken:
    """Someone new (full_name) or already in the roster (person_id) in a unit, by hand.
    Their phones are trusted (verified). If a number is another person's, nothing is saved."""
    role = PersonRole(role)
    _active_unit(session, unit_id)
    person = _person(session, person_id) if person_id is not None else None
    name = "" if person is not None else _full_name(full_name)

    numbers: dict[str, NormalizedPhone] = {}
    for raw in phones:
        if raw and raw.strip():
            phone = _parse_phone(raw)
            numbers.setdefault(phone.e164, phone)
    for e164 in list(numbers):
        mine = person is not None and any(p.e164 == e164 for p in person.phones)
        if mine:
            del numbers[e164]  # already theirs: nothing to add
        elif taken := _taken(session, e164, None):
            return taken

    if person is None:
        person = Person(full_name=name)
        session.add(person)
        session.flush()
    elif session.get(UnitPerson, (unit_id, person.id, role)) is not None:
        raise NeighborError("Esa persona ya está en esa unidad con ese rol.")

    session.add(
        UnitPerson(unit_id=unit_id, person_id=person.id, role=role, source=DataSource.MANUAL)
    )
    for phone in numbers.values():
        session.add(_manual_phone(person.id, phone))
    warnings = [w for e164 in numbers if (w := provider_warning(session, e164))]
    log_admin_action(
        session,
        user,
        "neighbor_added",
        person_id=person.id,
        unit_id=unit_id,
        role=str(role),
        new_person=person_id is None,
        phones=len(numbers),
    )
    session.commit()
    return PersonSaved(person.id, warnings)


def update_person(session: Session, user: str, person_id: int, full_name: str) -> None:
    """The name shown everywhere. ConsorPlus' name (consorplus_name) stays: the sync uses it."""
    person = _person(session, person_id)
    person.full_name = _full_name(full_name)
    log_admin_action(session, user, "neighbor_renamed", person_id=person.id)
    session.commit()


def _replace_link(
    session: Session, user: str, link: UnitPerson, *, unit_id: int, role: PersonRole
) -> DataSource:
    """The link moved to (unit_id, role): role and unit are in its key, so it is a new row.
    From ConsorPlus: the old one is suppressed and the new one is manual."""
    if session.get(UnitPerson, (unit_id, link.person_id, role)) is not None:
        raise NeighborError("Esa persona ya está en esa unidad con ese rol.")
    source = link.source
    if source == DataSource.CONSORPLUS:
        _suppress_link(session, user, link)
        source = DataSource.MANUAL
    person_id = link.person_id
    session.delete(link)
    session.flush()
    session.add(UnitPerson(unit_id=unit_id, person_id=person_id, role=role, source=source))
    return source


def change_role(
    session: Session,
    user: str,
    unit_id: int,
    person_id: int,
    role: PersonRole,
    new_role: PersonRole,
) -> None:
    link = _link(session, unit_id, person_id, PersonRole(role))
    new_role = PersonRole(new_role)
    if new_role == link.role:
        raise NeighborError("Ya tiene ese rol en la unidad.")
    old_source = str(link.source)
    _replace_link(session, user, link, unit_id=unit_id, role=new_role)
    log_admin_action(
        session,
        user,
        "neighbor_role_changed",
        person_id=person_id,
        unit_id=unit_id,
        role=str(role),
        new_role=str(new_role),
        source=old_source,
    )
    session.commit()


def move_unit(
    session: Session,
    user: str,
    unit_id: int,
    person_id: int,
    role: PersonRole,
    new_unit_id: int,
) -> None:
    link = _link(session, unit_id, person_id, PersonRole(role))
    if new_unit_id == unit_id:
        raise NeighborError("Ya está en esa unidad.")
    _active_unit(session, new_unit_id)
    old_source = str(link.source)
    _replace_link(session, user, link, unit_id=new_unit_id, role=link.role)
    log_admin_action(
        session,
        user,
        "neighbor_moved",
        person_id=person_id,
        unit_id=unit_id,
        new_unit_id=new_unit_id,
        role=str(role),
        source=old_source,
    )
    session.commit()


def remove_from_unit(
    session: Session, user: str, unit_id: int, person_id: int, role: PersonRole
) -> bool:
    """The person leaves the unit. Without any unit left, their phones and the person are
    deleted too (claims and verifications keep their own copy of the name). True if so."""
    link = _link(session, unit_id, person_id, PersonRole(role))
    person = link.person
    source = str(link.source)
    building_id = link.unit.building_id
    if link.source == DataSource.CONSORPLUS:
        _suppress_link(session, user, link)
    session.delete(link)
    session.flush()
    left = session.scalar(
        select(func.count()).select_from(UnitPerson).where(UnitPerson.person_id == person_id)
    )
    phones = 0
    if not left:
        for phone in list(person.phones):
            if phone.source == DataSource.CONSORPLUS:
                _suppress_phone(session, user, phone, building_id)
            phones += 1
        session.delete(person)
    log_admin_action(
        session,
        user,
        "neighbor_removed",
        person_id=person_id,
        unit_id=unit_id,
        role=str(role),
        source=source,
        person_deleted=not left,
        phones_deleted=phones,
    )
    session.commit()
    return not left


# --- Phones ----------------------------------------------------------------------------------


def _manual_phone(person_id: int, phone: NormalizedPhone) -> Phone:
    return Phone(
        person_id=person_id,
        e164=phone.e164,
        raw=phone.raw,
        source=DataSource.MANUAL,
        verified=True,
        needs_review=False,
        conflict=False,
    )


def add_phone(session: Session, user: str, person_id: int, raw: str) -> PhoneSaved | PhoneTaken:
    person = _person(session, person_id)
    normalized = _parse_phone(raw)
    if taken := _taken(session, normalized.e164, person.id):
        return taken
    phone = _manual_phone(person.id, normalized)
    session.add(phone)
    session.flush()
    warning = provider_warning(session, phone.e164)
    log_admin_action(
        session,
        user,
        "phone_added",
        phone_e164=phone.e164,
        phone_id=phone.id,
        person_id=person.id,
    )
    session.commit()
    return PhoneSaved(phone.id, warning)


def remove_phone(session: Session, user: str, phone_id: int) -> bool:
    """The phone is deleted (the person stays). From ConsorPlus: it does not come back."""
    phone = session.get(Phone, phone_id, with_for_update=True)
    if phone is None:
        return False
    suppressed = phone.source == DataSource.CONSORPLUS
    if suppressed:
        _suppress_phone(session, user, phone)
    log_admin_action(
        session,
        user,
        "phone_unlinked",
        phone_e164=phone.e164,
        phone_id=phone.id,
        person_id=phone.person_id,
        source=str(phone.source),
        verified=phone.verified,
        suppressed=suppressed,
    )
    session.delete(phone)
    session.commit()
    return True


def move_phone(session: Session, user: str, phone_id: int, person_id: int) -> PhoneSaved:
    """The number is now this person's (after PhoneTaken), trusted and loaded by hand."""
    phone = session.get(Phone, phone_id, with_for_update=True)
    if phone is None:
        raise NeighborError("Ese número ya no está en el padrón.")
    target = _person(session, person_id)
    if phone.person_id == target.id:
        raise NeighborError("Esa persona ya tiene ese número.")
    old_person_id, old_source = phone.person_id, str(phone.source)
    if phone.source == DataSource.CONSORPLUS:
        _suppress_phone(session, user, phone)
    phone.person = target
    phone.source = DataSource.MANUAL
    phone.verified = True
    phone.needs_review = False
    phone.conflict = False
    warning = provider_warning(session, phone.e164)
    log_admin_action(
        session,
        user,
        "phone_moved",
        phone_e164=phone.e164,
        phone_id=phone.id,
        person_id=target.id,
        from_person_id=old_person_id,
        source=old_source,
    )
    session.commit()
    return PhoneSaved(phone.id, warning)


# --- Read one building again -----------------------------------------------------------------


def refresh_building(
    session: Session,
    user: str,
    building_code: str,
    *,
    source: RosterSource | None = None,
    engine: Engine | None = None,
) -> dict[str, Any]:
    """Read the roster of ONE building from ConsorPlus now (read only, like every night).
    Recorded as a LIVE sync run: it does not count as last night's. Returns its counters."""
    code = building_code.strip()
    building = (
        session.scalar(select(Building).where(Building.consorplus_code == int(code)))
        if code.isdigit()
        else None
    )
    if building is None:
        raise NeighborError("No existe ese edificio.")
    try:
        with advisory_lock(engine or default_engine):
            if source is None:
                source = ConsorPlusClient.from_settings(get_settings())
            report = sync_roster(session, source, [code], kind=SyncKind.LIVE)
    except AlreadyRunningError:
        raise NeighborError("Ya se está sincronizando, probá en unos minutos.") from None
    log_admin_action(
        session,
        user,
        "roster_refreshed",
        building_id=building.id,
        status=str(report.status),
    )
    session.commit()
    return report.counters()
