"""Roster sync with the roster edited by hand (Vecinos): what was removed or changed in the
panel survives the nightly sync. All data here is invented."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin import neighbors_service as svc
from app.admin.phones import unlink_phone
from app.consorplus.models import RosterContact
from app.db.models import (
    DataSource,
    Person,
    PersonRole,
    Phone,
    RosterSuppression,
    SuppressionKind,
    Unit,
    UnitPerson,
)
from app.sync.roster import sync_roster
from tests.sync.test_roster_sync import ANA, JUAN, MARIA, FakeSource, count, links, row

USER = "operadora"
JUAN_PHONE = "+5493515550101"
ANA_PHONE = "+5493515550303"


def _sync(session: Session, source: FakeSource):
    report = sync_roster(session, source, ["7"])
    session.expire_all()
    return report


def _unit_id(session: Session, unit_value: str) -> int:
    return session.scalar(select(Unit.id).where(Unit.consorplus_unit_value == unit_value))


def _person_id(session: Session, consorplus_name: str) -> int:
    return session.scalar(select(Person.id).where(Person.consorplus_name == consorplus_name))


def _phone(session: Session, e164: str) -> Phone | None:
    return session.scalar(select(Phone).where(Phone.e164 == e164))


def test_removed_link_and_phone_are_not_recreated(db_session) -> None:
    source = FakeSource({"7": [row("9001", JUAN, tenant=ANA)]})
    _sync(db_session, source)
    unit_id = _unit_id(db_session, "9001")

    deleted = svc.remove_from_unit(
        db_session, USER, unit_id, _person_id(db_session, "LOPEZ ANA"), PersonRole.TENANT
    )
    svc.remove_phone(db_session, USER, _phone(db_session, JUAN_PHONE).id)
    assert deleted  # Ana had no other unit: she and her phone are gone

    for _ in range(2):
        report = _sync(db_session, source)
        assert links(db_session, "9001") == {
            ("PEREZ, JUAN", PersonRole.OWNER, DataSource.CONSORPLUS)
        }
        assert _phone(db_session, JUAN_PHONE) is None
        assert _phone(db_session, ANA_PHONE) is None
        assert count(db_session, Person) == 1
        assert (report.links_suppressed, report.phones_suppressed) == (1, 1)
    assert "PEREZ" not in str(report.stats()) and "LOPEZ" not in str(report.stats())


def test_restoring_brings_them_back_with_the_next_sync(db_session) -> None:
    source = FakeSource({"7": [row("9001", JUAN, tenant=ANA)]})
    _sync(db_session, source)
    unit_id = _unit_id(db_session, "9001")
    svc.remove_from_unit(
        db_session, USER, unit_id, _person_id(db_session, "LOPEZ ANA"), PersonRole.TENANT
    )
    svc.remove_phone(db_session, USER, _phone(db_session, JUAN_PHONE).id)

    for suppression in db_session.scalars(select(RosterSuppression)).all():
        assert svc.restore_suppression(db_session, USER, suppression.id)
    report = _sync(db_session, source)

    assert links(db_session, "9001") == {
        ("PEREZ, JUAN", PersonRole.OWNER, DataSource.CONSORPLUS),
        ("LOPEZ ANA", PersonRole.TENANT, DataSource.CONSORPLUS),
    }
    assert _phone(db_session, JUAN_PHONE) is not None
    assert _phone(db_session, ANA_PHONE) is not None
    assert (report.links_suppressed, report.phones_suppressed) == (0, 0)


def test_a_corrected_name_does_not_duplicate_the_person(db_session) -> None:
    source = FakeSource({"7": [row("9001", JUAN), row("9002", MARIA)]})
    _sync(db_session, source)
    juan_id = _person_id(db_session, "PEREZ, JUAN")

    svc.update_person(db_session, USER, juan_id, "Juan Pérez")
    _sync(db_session, source)

    juan = db_session.get(Person, juan_id)
    assert (juan.full_name, juan.consorplus_name) == ("Juan Pérez", "PEREZ, JUAN")
    assert count(db_session, Person) == 2
    assert links(db_session, "9001") == {("Juan Pérez", PersonRole.OWNER, DataSource.CONSORPLUS)}


def test_another_person_in_consorplus_is_a_real_change(db_session) -> None:
    source = FakeSource({"7": [row("9001", JUAN, tenant=ANA)]})
    _sync(db_session, source)
    svc.remove_from_unit(
        db_session,
        USER,
        _unit_id(db_session, "9001"),
        _person_id(db_session, "LOPEZ ANA"),
        PersonRole.TENANT,
    )

    source.rosters["7"] = [row("9001", JUAN, tenant=MARIA)]
    report = _sync(db_session, source)

    assert ("GOMEZ MARIA", PersonRole.TENANT, DataSource.CONSORPLUS) in links(db_session, "9001")
    assert report.links_suppressed == 0


def test_changed_role_of_a_consorplus_link_stays(db_session) -> None:
    source = FakeSource({"7": [row("9001", JUAN)]})
    _sync(db_session, source)
    unit_id = _unit_id(db_session, "9001")
    juan_id = _person_id(db_session, "PEREZ, JUAN")

    svc.change_role(db_session, USER, unit_id, juan_id, PersonRole.OWNER, PersonRole.RESIDENT)
    for _ in range(2):
        report = _sync(db_session, source)
        assert links(db_session, "9001") == {
            ("PEREZ, JUAN", PersonRole.RESIDENT, DataSource.MANUAL)
        }
        assert report.links_suppressed == 1
        assert _phone(db_session, JUAN_PHONE) is not None  # his phones still sync


def test_suppressed_link_keeps_the_phones_of_someone_still_in_the_roster(db_session) -> None:
    # The same Juan (same email) owns 9002 with another phone: the 9001 row is the only one
    # that lists 0101, and its link was removed by hand.
    juan_9002 = RosterContact(name="PEREZ, JUAN", mobile="3515550888", email="juan@example.com")
    source = FakeSource({"7": [row("9001", JUAN), row("9002", juan_9002)]})
    _sync(db_session, source)
    juan_id = _person_id(db_session, "PEREZ, JUAN")

    gone = svc.remove_from_unit(
        db_session, USER, _unit_id(db_session, "9001"), juan_id, PersonRole.OWNER
    )
    for _ in range(2):
        report = _sync(db_session, source)

    assert not gone
    assert links(db_session, "9001") == set()
    assert links(db_session, "9002") == {("PEREZ, JUAN", PersonRole.OWNER, DataSource.CONSORPLUS)}
    phones = {p.e164 for p in db_session.scalars(select(Phone).where(Phone.person_id == juan_id))}
    assert phones == {JUAN_PHONE, "+5493515550888"}
    assert report.phones_removed == 0


def test_nameless_contact_with_a_suppressed_phone(db_session) -> None:
    first = RosterContact(name="", phone="3515550505")
    second = RosterContact(name="", phone="3515550606")
    source = FakeSource({"7": [row("9001", first), row("9002", second)]})
    _sync(db_session, source)
    svc.remove_phone(db_session, USER, _phone(db_session, "+5493515550505").id)
    [suppression] = db_session.scalars(select(RosterSuppression)).all()
    assert (suppression.kind, suppression.name_key) == (SuppressionKind.PHONE, "")

    for _ in range(2):
        report = _sync(db_session, source)

    assert _phone(db_session, "+5493515550505") is None
    assert _phone(db_session, "+5493515550606") is not None  # the other nameless one
    assert count(db_session, Person) == 2  # no new nameless person each night
    assert len(links(db_session, "9001")) == 1 and len(links(db_session, "9002")) == 1
    assert report.phones_suppressed == 1


def test_unlink_in_phones_page_does_not_come_back(db_session) -> None:
    source = FakeSource({"7": [row("9001", JUAN)]})
    _sync(db_session, source)

    assert unlink_phone(db_session, _phone(db_session, JUAN_PHONE).id, USER)
    _sync(db_session, source)

    assert _phone(db_session, JUAN_PHONE) is None
    assert links(db_session, "9001") == {("PEREZ, JUAN", PersonRole.OWNER, DataSource.CONSORPLUS)}


def test_a_moved_unit_stays(db_session) -> None:
    source = FakeSource({"7": [row("9001", JUAN), row("9002", MARIA)]})
    _sync(db_session, source)

    svc.move_unit(
        db_session,
        USER,
        _unit_id(db_session, "9001"),
        _person_id(db_session, "PEREZ, JUAN"),
        PersonRole.OWNER,
        _unit_id(db_session, "9002"),
    )
    _sync(db_session, source)

    assert links(db_session, "9001") == set()
    assert ("PEREZ, JUAN", PersonRole.OWNER, DataSource.MANUAL) in links(db_session, "9002")
    assert count(db_session, UnitPerson) == 2
