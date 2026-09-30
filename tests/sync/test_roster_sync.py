"""Roster sync against the Postgres test database. All data here is invented."""

from dataclasses import dataclass, field

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.consorplus.errors import ConsorPlusUnavailableError
from app.consorplus.models import Building as CpBuilding
from app.consorplus.models import RosterContact, RosterRow
from app.db.models import (
    Building,
    DataSource,
    Person,
    PersonRole,
    Phone,
    SyncJob,
    SyncRun,
    SyncStatus,
    Unit,
    UnitPerson,
)
from app.sync.roster import sync_roster


def row(
    unit: str,
    owner: RosterContact | None = None,
    *,
    building: str = "007",
    unit_type: str = "DPTO",
    second_owner: RosterContact | None = None,
    tenant: RosterContact | None = None,
    payment_code: str = "",
) -> RosterRow:
    return RosterRow(
        unit_value=unit,
        building_code=building,
        building_name=f"{building} CONSORCIO FICTICIO",
        unit_label=f"{unit[-2:]}° A",
        ph=unit[-1],
        unit_type=unit_type,
        owner=owner,
        second_owner=second_owner,
        tenant=tenant,
        payment_code=payment_code,
    )


JUAN = RosterContact(name="PEREZ, JUAN", phone="0351-15-5550101", email="juan@example.com")
MARIA = RosterContact(name="GOMEZ MARIA", mobile="3515550202", document="20.100.200")
ANA = RosterContact(name="LOPEZ ANA", phone="3515550303")


@dataclass
class FakeSource:
    rosters: dict[str, list[RosterRow] | Exception] = field(default_factory=dict)

    def list_buildings(self) -> list[CpBuilding]:
        return [CpBuilding(code=code, name=f"{code} X") for code in self.rosters]

    def list_roster(self, building_code: str) -> list[RosterRow]:
        result = self.rosters[building_code]
        if isinstance(result, Exception):
            raise result
        return result


def count(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def links(session: Session, unit_value: str) -> set[tuple[str, PersonRole, DataSource]]:
    stmt = (
        select(Person.full_name, UnitPerson.role, UnitPerson.source)
        .join(UnitPerson, UnitPerson.person_id == Person.id)
        .join(Unit, Unit.id == UnitPerson.unit_id)
        .where(Unit.consorplus_unit_value == unit_value)
    )
    return {tuple(r) for r in session.execute(stmt)}


@pytest.fixture
def source() -> FakeSource:
    return FakeSource({"7": [row("9001", JUAN, second_owner=MARIA, tenant=ANA), row("9002", JUAN)]})


def test_sync_creates_buildings_units_people_links_and_phones(db_session, source) -> None:
    report = sync_roster(db_session, source, ["7"])

    building = db_session.scalar(select(Building))
    assert building.consorplus_code == 7
    assert building.name == "007 CONSORCIO FICTICIO"
    assert count(db_session, Unit) == 2
    assert count(db_session, Person) == 3  # JUAN owns two units: one person
    assert links(db_session, "9001") == {
        ("PEREZ, JUAN", PersonRole.OWNER, DataSource.CONSORPLUS),
        ("GOMEZ MARIA", PersonRole.OWNER, DataSource.CONSORPLUS),
        ("LOPEZ ANA", PersonRole.TENANT, DataSource.CONSORPLUS),
    }
    assert links(db_session, "9002") == {("PEREZ, JUAN", PersonRole.OWNER, DataSource.CONSORPLUS)}

    phones = {p.e164: p for p in db_session.scalars(select(Phone))}
    assert set(phones) == {"+5493515550101", "+5493515550202", "+5493515550303"}
    juan_phone = phones["+5493515550101"]
    assert juan_phone.raw == "0351-15-5550101"
    assert juan_phone.source == DataSource.CONSORPLUS
    assert juan_phone.verified is False
    maria = db_session.scalar(select(Person).where(Person.full_name == "GOMEZ MARIA"))
    assert maria.dni == "20100200"
    juan = db_session.scalar(select(Person).where(Person.full_name == "PEREZ, JUAN"))
    assert juan.email == "juan@example.com"

    assert (report.units, report.people, report.people_created) == (2, 3, 3)
    assert (report.phones_valid, report.phones_invalid, report.conflicts) == (3, 0, 0)
    run = db_session.scalar(select(SyncRun))
    assert (run.job, run.status, run.units_ok) == (SyncJob.ROSTER, SyncStatus.OK, 2)
    assert run.finished_at is not None
    assert run.stats["units"] == 2
    assert run.stats["people"] == 3


def test_sync_is_idempotent(db_session, source) -> None:
    sync_roster(db_session, source, ["7"])
    totals = [count(db_session, m) for m in (Building, Unit, Person, UnitPerson, Phone)]

    second = sync_roster(db_session, source, ["7"])

    assert [count(db_session, m) for m in (Building, Unit, Person, UnitPerson, Phone)] == totals
    assert (second.people_created, second.links_added, second.links_removed) == (0, 0, 0)
    assert second.conflicts == 0
    assert count(db_session, SyncRun) == 2


def test_same_person_by_dni_even_if_name_is_spelled_differently(db_session) -> None:
    other_spelling = RosterContact(name="MARIA E. GOMEZ", document="20100200")
    source = FakeSource({"7": [row("9001", MARIA), row("9002", other_spelling)]})

    sync_roster(db_session, source, ["7"])

    assert count(db_session, Person) == 1


def test_same_name_without_common_phone_or_email_is_another_person(db_session) -> None:
    namesake = RosterContact(name="JUAN PEREZ", phone="3515559999")
    source = FakeSource({"7": [row("9001", JUAN), row("9002", namesake)]})

    sync_roster(db_session, source, ["7"])

    assert count(db_session, Person) == 2


def test_owner_change_replaces_only_consorplus_links(db_session) -> None:
    sync_roster(db_session, FakeSource({"7": [row("9001", JUAN, tenant=ANA)]}), ["7"])
    unit = db_session.scalar(select(Unit))
    verified = Person(full_name="PERSONA VERIFICADA")
    manual = Person(full_name="PERSONA MANUAL")
    db_session.add_all([verified, manual])
    db_session.flush()
    db_session.add_all(
        [
            UnitPerson(
                unit_id=unit.id,
                person_id=verified.id,
                role=PersonRole.OWNER,
                source=DataSource.BOT_VERIFIED,
            ),
            UnitPerson(
                unit_id=unit.id,
                person_id=manual.id,
                role=PersonRole.TENANT,
                source=DataSource.MANUAL,
            ),
        ]
    )
    db_session.commit()

    report = sync_roster(db_session, FakeSource({"7": [row("9001", MARIA)]}), ["7"])

    assert links(db_session, "9001") == {
        ("GOMEZ MARIA", PersonRole.OWNER, DataSource.CONSORPLUS),
        ("PERSONA VERIFICADA", PersonRole.OWNER, DataSource.BOT_VERIFIED),
        ("PERSONA MANUAL", PersonRole.TENANT, DataSource.MANUAL),
    }
    assert (report.links_added, report.links_removed) == (1, 2)
    # The previous owner is unlinked, not deleted (they may own other units or have chats).
    assert db_session.scalar(select(Person).where(Person.full_name == "PEREZ, JUAN")) is not None
    assert db_session.scalar(select(Unit)).owner_name == "GOMEZ MARIA"


def test_phone_of_another_person_is_flagged_not_moved(db_session) -> None:
    other = Person(full_name="OTRA PERSONA")
    db_session.add(other)
    db_session.flush()
    existing = Phone(person_id=other.id, e164="+5493515550101", source=DataSource.MANUAL)
    db_session.add(existing)
    db_session.commit()

    report = sync_roster(db_session, FakeSource({"7": [row("9001", JUAN)]}), ["7"])

    db_session.refresh(existing)
    assert existing.person_id == other.id
    assert (existing.conflict, existing.needs_review) == (True, False)
    assert count(db_session, Phone) == 1
    assert report.conflicts == 1
    assert count(db_session, Person) == 2


def test_assumed_area_code_is_flagged_and_invalid_phones_are_counted(db_session) -> None:
    local = RosterContact(name="RODRIGUEZ PEDRO", phone="5550404", mobile="no tiene 12")
    no_phone = RosterContact(name="FERNANDEZ LUIS", email="luis@example.com")
    source = FakeSource({"7": [row("9001", local), row("9002", no_phone)]})

    report = sync_roster(db_session, source, ["7"])

    phone = db_session.scalar(select(Phone))
    assert (phone.e164, phone.raw, phone.needs_review) == ("+5493515550404", "5550404", True)
    assert phone.conflict is False
    assert (report.needs_review, report.phones_invalid) == (1, 1)
    assert report.units_without_owner_phone == 1


def test_contact_without_name_nor_phone_nor_email_is_ignored(db_session) -> None:
    empty = RosterContact(name="", document="20.100.200")
    source = FakeSource({"7": [row("9001", empty)]})

    report = sync_roster(db_session, source, ["7"])

    assert count(db_session, Person) == 0
    assert (report.contacts_ignored, report.nameless_contacts) == (1, 0)
    assert report.units_without_owner_phone == 1


VALID_CODE = "0000000000000009001"


def test_payment_code_is_stored_only_when_it_has_19_digits(db_session) -> None:
    source = FakeSource(
        {
            "7": [
                row("9001", JUAN, payment_code=VALID_CODE),
                row("9002", ANA, payment_code="12345-6"),
                row("9003", MARIA),
            ]
        }
    )

    report = sync_roster(db_session, source, ["7"])

    codes = dict(db_session.execute(select(Unit.consorplus_unit_value, Unit.payment_code)).all())
    assert codes == {"9001": VALID_CODE, "9002": None, "9003": None}
    counters = (report.payment_codes_valid, report.payment_codes_invalid)
    assert counters + (report.payment_codes_missing,) == (1, 1, 1)
    run = db_session.scalar(select(SyncRun))
    assert run.stats["payment_codes_valid"] == 1
    # Counters only: the code itself never reaches the report or sync_runs.
    assert VALID_CODE not in repr(report)
    assert VALID_CODE not in str(run.stats)


def test_payment_code_that_turns_invalid_is_cleared(db_session) -> None:
    sync_roster(db_session, FakeSource({"7": [row("9001", JUAN, payment_code=VALID_CODE)]}), ["7"])

    report = sync_roster(
        db_session, FakeSource({"7": [row("9001", JUAN, payment_code="999")]}), ["7"]
    )

    assert db_session.scalar(select(Unit.payment_code)) is None
    assert report.payment_codes_invalid == 1


def test_one_failing_building_does_not_stop_the_rest(db_session) -> None:
    source = FakeSource(
        {
            "7": ConsorPlusUnavailableError("sin respuesta"),
            "8": [row("9101", ANA, building="008")],
        }
    )

    report = sync_roster(db_session, source)

    assert (report.buildings_ok, report.buildings_failed) == (1, 1)
    assert count(db_session, Unit) == 1
    run = db_session.scalar(select(SyncRun))
    assert run.status == SyncStatus.PARTIAL
    assert "edificio 7" in run.error_summary


def test_db_error_in_a_building_rolls_back_only_that_building(db_session) -> None:
    source = FakeSource(
        {
            "7": [row("9001", JUAN), row("9002", ANA)],
            "8": [row("9101", ANA, building="008")],
            "x": [row("9201", MARIA, building="x")],  # int("x") fails
        }
    )

    report = sync_roster(db_session, source)

    assert (report.buildings_ok, report.buildings_failed) == (2, 1)
    assert count(db_session, Building) == 2
    assert report.units == 3  # the failed building's counters are discarded


def test_all_buildings_failing_is_failed(db_session) -> None:
    source = FakeSource({"7": ConsorPlusUnavailableError("sin respuesta")})

    report = sync_roster(db_session, source)

    assert report.status == SyncStatus.FAILED
    assert db_session.scalar(select(SyncRun)).status == SyncStatus.FAILED


def test_dry_run_writes_nothing(db_session, source) -> None:
    report = sync_roster(db_session, source, ["7"], dry_run=True)

    assert (report.units, report.people, report.phones_valid) == (2, 3, 3)
    for model in (Building, Unit, Person, UnitPerson, Phone, SyncRun):
        assert count(db_session, model) == 0
