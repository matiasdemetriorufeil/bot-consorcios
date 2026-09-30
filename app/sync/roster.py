"""Idempotent sync of the roster (buildings, units, people, phones) from ConsorPlus.

Only links and phones with source="consorplus" are created, replaced or removed here;
"bot_verified" and "manual" data is never touched. Logs and reports hold counters only,
never personal data.
"""

import copy
import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.consorplus.errors import ConsorPlusError
from app.consorplus.models import Building as CpBuilding
from app.consorplus.models import RosterContact, RosterRow
from app.db.models import (
    Building,
    DataSource,
    Person,
    PersonRole,
    Phone,
    SyncJob,
    SyncKind,
    SyncRun,
    SyncStatus,
    Unit,
    UnitPerson,
)
from app.sync.normalize import (
    NormalizedPhone,
    clean_document,
    clean_name,
    extract_emails,
    extract_phones,
    name_key,
)

logger = logging.getLogger(__name__)

MAX_ERROR_SUMMARY = 4000
NAMELESS_PERSON = "(sin nombre en ConsorPlus)"
NO_UNIT_TYPE = "(sin tipo)"


class RosterSource(Protocol):
    """What the sync needs from ConsorPlusClient (a fake in tests)."""

    def list_buildings(self) -> list[CpBuilding]: ...

    def list_roster(self, building_code: str) -> list[RosterRow]: ...


def _unit_type_counters() -> dict[str, int]:
    return {"units": 0, "with_owner_phone": 0, "with_owner_email": 0}


@dataclass
class RosterReport:
    """Counters only: safe to print and to store in sync_runs.stats."""

    buildings_ok: int = 0
    buildings_failed: int = 0
    units: int = 0
    units_deactivated: int = 0
    people: int = 0
    people_created: int = 0
    phones_valid: int = 0
    phones_invalid: int = 0
    phones_removed: int = 0
    needs_review: int = 0
    units_without_owner_phone: int = 0
    conflicts: int = 0
    links_added: int = 0
    links_removed: int = 0
    nameless_contacts: int = 0
    contacts_ignored: int = 0
    people_in_several_buildings: int = 0
    # unit type -> {"units", "with_owner_phone", "with_owner_email"}
    by_unit_type: dict[str, dict[str, int]] = field(
        default_factory=lambda: defaultdict(_unit_type_counters)
    )
    errors: list[str] = field(default_factory=list)
    # Not counters: kept out of the report and of sync_runs.stats.
    _people_ids: set[int] = field(default_factory=set, repr=False)
    _phones_seen: set[str] = field(default_factory=set, repr=False)

    @property
    def status(self) -> SyncStatus:
        if self.buildings_failed == 0:
            return SyncStatus.OK
        return SyncStatus.PARTIAL if self.buildings_ok else SyncStatus.FAILED

    def counters(self) -> dict[str, int]:
        return {k: v for k, v in asdict(self).items() if isinstance(v, int)}

    def stats(self) -> dict[str, Any]:
        by_type = {k: dict(v) for k, v in sorted(self.by_unit_type.items())}
        return {**self.counters(), "by_unit_type": by_type}


@dataclass(frozen=True)
class _Contact:
    """A normalized ConsorPlus contact. Nameless ones carry NAMELESS_PERSON as name."""

    name: str
    named: bool
    key: str
    dni: str | None
    phones: tuple[NormalizedPhone, ...]
    emails: tuple[str, ...]


def _normalize_contact(contact: RosterContact, report: RosterReport) -> _Contact | None:
    name = clean_name(contact.name)
    phones: list[NormalizedPhone] = []
    for cell in (contact.phone, contact.mobile):
        found, invalid = extract_phones(cell)
        report.phones_invalid += invalid
        phones.extend(p for p in found if p.e164 not in {q.e164 for q in phones})
    emails = extract_emails(contact.email)
    if not name:
        if not phones and not emails:
            # Nothing to show, match on or reach: skipped.
            report.contacts_ignored += 1
            return None
        report.nameless_contacts += 1
    return _Contact(
        name=name or NAMELESS_PERSON,
        named=bool(name),
        key=name_key(name) if name else "",
        dni=clean_document(contact.document),
        phones=tuple(phones),
        emails=tuple(emails),
    )


def _upsert_building(session: Session, code: int, name: str) -> Building:
    building = session.scalar(select(Building).where(Building.consorplus_code == code))
    if building is None:
        building = Building(consorplus_code=code, name=name or f"Edificio {code}")
        session.add(building)
    elif name and building.name != name:
        building.name = name
    session.flush()
    return building


def _upsert_unit(session: Session, building: Building, row: RosterRow) -> Unit:
    unit = session.scalar(
        select(Unit).where(
            Unit.building_id == building.id, Unit.consorplus_unit_value == row.unit_value
        )
    )
    label = clean_name(row.unit_label) or row.unit_value
    owner_name = clean_name(row.owner.name) if row.owner else None
    if unit is None:
        unit = Unit(building_id=building.id, consorplus_unit_value=row.unit_value, label=label)
        session.add(unit)
    unit.label = label
    unit.unit_type = clean_name(row.unit_type) or None
    unit.owner_name = owner_name or None
    unit.active = True
    session.flush()
    return unit


def _people_sharing_contact(session: Session, contact: _Contact) -> list[Person]:
    """People that already have one of the contact's phones or emails."""
    people: list[Person] = []
    if contact.phones:
        people += session.scalars(
            select(Person)
            .join(Phone, Phone.person_id == Person.id)
            .where(Phone.e164.in_([p.e164 for p in contact.phones]))
        ).all()
    if contact.emails:
        people += session.scalars(
            select(Person).where(func.lower(Person.email).in_(contact.emails))
        ).all()
    return people


def _find_person(
    session: Session, unit: Unit, role: PersonRole, contact: _Contact
) -> Person | None:
    if not contact.named:
        # Nameless contacts are deduplicated only by phone/email, among nameless people.
        return next(
            (
                p
                for p in _people_sharing_contact(session, contact)
                if p.full_name == NAMELESS_PERSON
            ),
            None,
        )

    # 1. The person already linked to this unit/role (keeps re-runs idempotent).
    linked = session.scalars(
        select(Person)
        .join(UnitPerson, UnitPerson.person_id == Person.id)
        .where(UnitPerson.unit_id == unit.id, UnitPerson.role == role)
    ).all()
    for person in linked:
        if contact.dni and person.dni:
            if person.dni == contact.dni:
                return person
        elif name_key(person.full_name) == contact.key:
            return person

    # 2. Same DNI.
    if contact.dni:
        person = session.scalar(select(Person).where(Person.dni == contact.dni).limit(1))
        if person is not None:
            return person

    # 3. Same normalized name AND a phone or email in common.
    for person in _people_sharing_contact(session, contact):
        if name_key(person.full_name) != contact.key:
            continue
        if contact.dni and person.dni and contact.dni != person.dni:
            continue
        return person
    return None


def _upsert_person(
    session: Session, unit: Unit, role: PersonRole, contact: _Contact, report: RosterReport
) -> Person:
    person = _find_person(session, unit, role, contact)
    if person is None:
        person = Person(full_name=contact.name, dni=contact.dni)
        session.add(person)
        report.people_created += 1
    if contact.dni and not person.dni:
        person.dni = contact.dni
    if contact.emails and not person.email:
        person.email = contact.emails[0]
    session.flush()
    report._people_ids.add(person.id)
    return person


def _sync_phones(
    session: Session, person: Person, phones: Iterable[NormalizedPhone], report: RosterReport
) -> None:
    for phone in phones:
        if phone.e164 not in report._phones_seen:
            report._phones_seen.add(phone.e164)
            report.phones_valid += 1
            report.needs_review += phone.needs_review
        existing = session.scalar(select(Phone).where(Phone.e164 == phone.e164))
        if existing is None:
            session.add(
                Phone(
                    person_id=person.id,
                    e164=phone.e164,
                    raw=phone.raw,
                    source=DataSource.CONSORPLUS,
                    verified=False,
                    needs_review=phone.needs_review,
                )
            )
        elif existing.person_id != person.id:
            # phones.e164 is unique: the number is already someone else's. Flag, do not move.
            existing.needs_review = True
            report.conflicts += 1
    session.flush()


def _sync_links(
    session: Session, unit: Unit, wanted: set[tuple[int, PersonRole]], report: RosterReport
) -> None:
    current = session.scalars(select(UnitPerson).where(UnitPerson.unit_id == unit.id)).all()
    present = {(link.person_id, link.role) for link in current}
    for link in current:
        if link.source == DataSource.CONSORPLUS and (link.person_id, link.role) not in wanted:
            session.delete(link)
            report.links_removed += 1
    for person_id, role in wanted - present:
        session.add(
            UnitPerson(
                unit_id=unit.id, person_id=person_id, role=role, source=DataSource.CONSORPLUS
            )
        )
        report.links_added += 1
    session.flush()
    session.expire(unit, ["people"])


def _deactivate_missing_units(
    session: Session, building: Building, rows: list[RosterRow], report: RosterReport
) -> None:
    if not rows:
        return  # an empty listing is more likely a glitch than a building without units
    listed = {row.unit_value for row in rows}
    for unit in session.scalars(
        select(Unit).where(Unit.building_id == building.id, Unit.active.is_(True))
    ):
        if unit.consorplus_unit_value not in listed:
            unit.active = False
            report.units_deactivated += 1
    session.flush()


def _remove_missing_phones(
    session: Session, building: Building, wanted: dict[int, set[str]], report: RosterReport
) -> None:
    """Delete the consorplus phones that ConsorPlus no longer lists for these people.

    People linked to units of other buildings are skipped: this building alone does not show
    all their phones.
    """
    for person_id, phones in wanted.items():
        elsewhere = session.scalar(
            select(func.count())
            .select_from(UnitPerson)
            .join(Unit, Unit.id == UnitPerson.unit_id)
            .where(
                UnitPerson.person_id == person_id,
                UnitPerson.source == DataSource.CONSORPLUS,
                Unit.building_id != building.id,
            )
        )
        if elsewhere:
            report.people_in_several_buildings += 1
            continue
        stale = session.scalars(
            select(Phone).where(
                Phone.person_id == person_id,
                Phone.source == DataSource.CONSORPLUS,
                Phone.e164.not_in(phones) if phones else Phone.e164.is_not(None),
            )
        ).all()
        for phone in stale:
            session.delete(phone)
            report.phones_removed += 1
    session.flush()


def sync_building_rows(
    session: Session, building_code: str, rows: list[RosterRow], report: RosterReport
) -> None:
    """Apply the ConsorPlus rows of ONE building to the database (no commit).

    Runs inside the building's savepoint: removals (units, phones) only stick if the whole
    building synced fine.
    """
    name = next((clean_name(r.building_name) for r in rows if r.building_name), "")
    building = _upsert_building(session, int(building_code), name)
    wanted_phones: dict[int, set[str]] = defaultdict(set)
    for row in rows:
        unit = _upsert_unit(session, building, row)
        report.units += 1
        wanted: set[tuple[int, PersonRole]] = set()
        owner_has_phone = owner_has_email = False
        contacts = (
            (PersonRole.OWNER, row.owner),
            (PersonRole.OWNER, row.second_owner),
            (PersonRole.TENANT, row.tenant),
        )
        for role, raw_contact in contacts:
            if raw_contact is None:
                continue
            contact = _normalize_contact(raw_contact, report)
            if contact is None:
                continue
            person = _upsert_person(session, unit, role, contact, report)
            _sync_phones(session, person, contact.phones, report)
            wanted_phones[person.id].update(p.e164 for p in contact.phones)
            wanted.add((person.id, role))
            if role == PersonRole.OWNER:
                owner_has_phone |= bool(contact.phones)
                owner_has_email |= bool(contact.emails)
        _sync_links(session, unit, wanted, report)
        report.units_without_owner_phone += not owner_has_phone
        by_type = report.by_unit_type[unit.unit_type or NO_UNIT_TYPE]
        by_type["units"] += 1
        by_type["with_owner_phone"] += owner_has_phone
        by_type["with_owner_email"] += owner_has_email

    _deactivate_missing_units(session, building, rows, report)
    _remove_missing_phones(session, building, wanted_phones, report)


def sync_roster(
    session: Session,
    source: RosterSource,
    building_codes: Iterable[str] | None = None,
    *,
    dry_run: bool = False,
) -> RosterReport:
    """Sync the given buildings (all of them if None). One failing building does not stop
    the rest. With dry_run nothing is written (everything is rolled back at the end)."""
    report = RosterReport()
    run: SyncRun | None = None
    if not dry_run:
        run = SyncRun(kind=SyncKind.NIGHTLY, job=SyncJob.ROSTER)
        session.add(run)
        session.commit()

    codes: list[str] = []
    try:
        codes = (
            list(building_codes)
            if building_codes is not None
            else [b.code for b in source.list_buildings()]
        )
    except ConsorPlusError as exc:
        report.buildings_failed += 1
        report.errors.append(f"listado de edificios: {type(exc).__name__}: {exc}")

    for code in codes:
        before = copy.deepcopy(report)
        try:
            rows = source.list_roster(code)
            with session.begin_nested():
                sync_building_rows(session, code, rows, report)
            if not dry_run:
                session.commit()
            report.buildings_ok += 1
        except Exception as exc:  # one building must not stop the others
            report = before  # its partial counters are discarded along with its changes
            report.buildings_failed += 1
            # Only our own messages: DB errors may echo row values (personal data).
            detail = f": {exc}" if isinstance(exc, ConsorPlusError) else ""
            report.errors.append(f"edificio {code}: {type(exc).__name__}{detail}")
            logger.warning("Roster sync failed for building %s: %s", code, type(exc).__name__)

    report.people = len(report._people_ids)
    if run is None:
        session.rollback()
        return report
    run.finished_at = datetime.now(UTC)
    run.status = report.status
    run.units_ok = report.units
    run.stats = report.stats()
    run.error_summary = "\n".join(report.errors)[:MAX_ERROR_SUMMARY] or None
    session.commit()
    return report
