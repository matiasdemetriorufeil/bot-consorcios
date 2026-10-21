"""The roster edited by hand ("Vecinos"): what the panel's page calls. No HTTP here.

Each function is one transaction (it commits) and leaves its trace in bot_events
(log_admin_action: ids, roles and sources, never names). With commit=False it only flushes, so
the panel can do several steps in one transaction (all or nothing: it commits once, or rolls
everything back if a step fails). Errors the operator must read are NeighborError, in Spanish.

A number given to someone here also approves its pending verification (the bot's «request an
operator»): it would be the same decision, taken from Vecinos.

What came from ConsorPlus and is removed or changed here leaves a roster_suppressions row, so
the roster sync (app.sync.roster) does not bring it back at night. A link or phone loaded by hand
(or verified by the bot) is changed in place: the sync never touches those.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, selectinload

from app.admin import formatting
from app.admin.audit import log_admin_action
from app.config import get_settings
from app.consorplus import ConsorPlusClient
from app.db.models import (
    BotEvent,
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
    VerificationRequest,
    VerificationRequestStatus,
)
from app.db.session import engine as default_engine
from app.person_roles import role_text
from app.sync.nightly import AlreadyRunningError, advisory_lock
from app.sync.normalize import NormalizedPhone, clean_name, name_key, normalize_phone
from app.sync.roster import RosterSource, consorplus_key, sync_roster

MAX_NAME = 200  # people.full_name
# «¿Es la misma persona…?»: from this many letters, and this close (rapidfuzz, 0-100).
SIMILAR_MIN_CHARS = 4
SIMILAR_SCORE = 88


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
    approved: int = 0  # pending verifications of their numbers, approved


@dataclass(frozen=True)
class PhoneSaved:
    phone_id: int
    warning: str | None = None
    approved: int = 0


def _finish(session: Session, commit: bool) -> None:
    """The end of a change: its own transaction, or one step of the panel's (flush only)."""
    if commit:
        session.commit()
    else:
        session.flush()


def _approve_pending(
    session: Session, user: str, e164: str, person_id: int, via: str = "vecinos"
) -> int:
    """The pending verifications of this number are approved with this person, as if the
    operator had approved them in Verificaciones (same event). How many."""
    pending = session.scalars(
        select(VerificationRequest)
        .where(
            VerificationRequest.phone_e164 == e164,
            VerificationRequest.status == VerificationRequestStatus.PENDING,
        )
        .with_for_update()
    ).all()
    for request in pending:
        request.status = VerificationRequestStatus.APPROVED
        request.person_id = person_id
        request.resolved_at = datetime.now(UTC)
        request.resolved_by = user
        session.add(
            BotEvent(
                phone_e164=e164,
                event_type="operator_verification_resolved",
                payload={
                    "result": "approved",
                    "request_id": request.id,
                    "unit_id": request.unit_id,
                    "person_id": person_id,
                    "resolved_by": user,
                    "via": via,
                },
            )
        )
    return len(pending)


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
        raise NeighborError("Falta la característica (por ejemplo 351).")
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


# --- Reads for the forms (nothing is written) ------------------------------------------------


@dataclass(frozen=True)
class PhoneCheck:
    """How a typed number would be saved, before saving it."""

    e164: str | None = None
    shown: str = ""  # as the panel shows it: "351 555-0101"
    error: str | None = None
    taken: PhoneTaken | None = None
    warning: str | None = None  # an active provider's WhatsApp (it does not block)


def check_phone(session: Session, raw: str, person_id: int | None = None) -> PhoneCheck:
    try:
        phone = _parse_phone(raw)
        taken = _taken(session, phone.e164, person_id)
    except NeighborError as exc:
        return PhoneCheck(error=str(exc))
    return PhoneCheck(
        e164=phone.e164,
        shown=formatting.phone(phone.e164),
        taken=taken,
        warning=provider_warning(session, phone.e164),
    )


@dataclass(frozen=True)
class SimilarPerson:
    person_id: int
    name: str
    places: list[str]  # units_text


def _folded(name: str) -> str:
    return " ".join(name_key(name).split()) if name else ""


def _closeness(wanted: str, words: set[str], name: str) -> float:
    """The whole name alike, in any order and with a typo («Juan Carlos Peres»), or every
    word she typed, two at least, in the name («María Gómez» in «GÓMEZ MARÍA ROSA»). A shared
    surname alone is not enough: relatives live in the same unit."""
    if len(words) >= 2 and words <= set(name.split()):
        return 100.0
    return fuzz.token_sort_ratio(wanted, name)


def similar_people(session: Session, name: str, *, limit: int = 3) -> list[SimilarPerson]:
    """People of the roster with the same or a very close name (same normalized name, or
    rapidfuzz like app.bot.unit_search): to link them instead of creating someone else."""
    wanted = _folded(clean_name(name or ""))
    if len(wanted.replace(" ", "")) < SIMILAR_MIN_CHARS:
        return []
    words = set(wanted.split())
    scored = []
    for person_id, full_name, cp_name in session.execute(
        select(Person.id, Person.full_name, Person.consorplus_name)
    ):
        score = max(_closeness(wanted, words, _folded(n)) for n in (full_name, cp_name) if n)
        if score >= SIMILAR_SCORE:
            scored.append((-score, full_name, person_id))
    ids = [person_id for _, _, person_id in sorted(scored)[:limit]]
    if not ids:
        return []
    people = session.scalars(
        select(Person)
        .where(Person.id.in_(ids))
        .options(
            selectinload(Person.units).selectinload(UnitPerson.unit).selectinload(Unit.building)
        )
    ).all()
    by_id = {p.id: p for p in people}
    return [
        SimilarPerson(by_id[i].id, by_id[i].full_name, units_text(by_id[i]))
        for i in ids
        if i in by_id
    ]


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
    commit: bool = True,
    via: str = "vecinos",
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
    session.flush()
    approved = sum(_approve_pending(session, user, e164, person.id, via) for e164 in numbers)
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
    _finish(session, commit)
    return PersonSaved(person.id, warnings, approved)


def update_person(
    session: Session, user: str, person_id: int, full_name: str, *, commit: bool = True
) -> None:
    """The name shown everywhere. ConsorPlus' name (consorplus_name) stays: the sync uses it."""
    person = _person(session, person_id)
    person.full_name = _full_name(full_name)
    log_admin_action(session, user, "neighbor_renamed", person_id=person.id)
    _finish(session, commit)


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
    *,
    commit: bool = True,
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
    _finish(session, commit)


def move_unit(
    session: Session,
    user: str,
    unit_id: int,
    person_id: int,
    role: PersonRole,
    new_unit_id: int,
    *,
    commit: bool = True,
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
    _finish(session, commit)


def remove_from_unit(
    session: Session,
    user: str,
    unit_id: int,
    person_id: int,
    role: PersonRole,
    *,
    commit: bool = True,
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
    _finish(session, commit)
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


def add_phone(
    session: Session, user: str, person_id: int, raw: str, *, commit: bool = True
) -> PhoneSaved | PhoneTaken:
    person = _person(session, person_id)
    normalized = _parse_phone(raw)
    if taken := _taken(session, normalized.e164, person.id):
        return taken
    phone = _manual_phone(person.id, normalized)
    session.add(phone)
    session.flush()
    approved = _approve_pending(session, user, phone.e164, person.id)
    warning = provider_warning(session, phone.e164)
    log_admin_action(
        session,
        user,
        "phone_added",
        phone_e164=phone.e164,
        phone_id=phone.id,
        person_id=person.id,
    )
    _finish(session, commit)
    return PhoneSaved(phone.id, warning, approved)


def remove_phone(session: Session, user: str, phone_id: int, *, commit: bool = True) -> bool:
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
    _finish(session, commit)
    return True


def move_phone(
    session: Session, user: str, phone_id: int, person_id: int, *, commit: bool = True
) -> PhoneSaved:
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
    phone.conflict_person_id = None
    session.flush()
    approved = _approve_pending(session, user, phone.e164, target.id)
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
    _finish(session, commit)
    return PhoneSaved(phone.id, warning, approved)


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
