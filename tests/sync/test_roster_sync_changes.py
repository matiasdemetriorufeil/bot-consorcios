"""Roster sync: unit types, nameless contacts and data that disappears. Invented data."""

from sqlalchemy import select

from app.consorplus.models import RosterContact
from app.db.models import Building, DataSource, Person, Phone, SyncRun, Unit, UnitPerson
from app.sync.roster import sync_roster
from tests.sync.test_roster_sync import ANA, JUAN, MARIA, FakeSource, count, row

JUAN_NEW_PHONE = RosterContact(name="PEREZ, JUAN", phone="3515550999", email="juan@example.com")


# --- Unit types --------------------------------------------------------------------------


def test_unit_type_is_stored_and_reported(db_session) -> None:
    no_contact = RosterContact(name="FERNANDEZ LUIS")
    source = FakeSource(
        {
            "7": [
                row("9001", JUAN),
                row("9002", MARIA, unit_type="COCH"),
                row("9003", no_contact, unit_type=""),
            ]
        }
    )

    report = sync_roster(db_session, source, ["7"])

    types = dict(db_session.execute(select(Unit.consorplus_unit_value, Unit.unit_type)).all())
    assert types == {"9001": "DPTO", "9002": "COCH", "9003": None}
    assert report.stats()["by_unit_type"] == {
        "(sin tipo)": {"units": 1, "with_owner_phone": 0, "with_owner_email": 0},
        "COCH": {"units": 1, "with_owner_phone": 1, "with_owner_email": 0},
        "DPTO": {"units": 1, "with_owner_phone": 1, "with_owner_email": 1},
    }
    assert db_session.scalar(select(SyncRun)).stats["by_unit_type"]["DPTO"]["units"] == 1


# --- Nameless contacts -------------------------------------------------------------------


def test_nameless_contact_with_phone_or_email_is_created(db_session) -> None:
    by_phone = RosterContact(name="", phone="3515550505")
    by_email = RosterContact(name="", email="Sin.Nombre@example.com")
    source = FakeSource({"7": [row("9001", by_phone), row("9002", tenant=by_email)]})

    report = sync_roster(db_session, source, ["7"])

    people = db_session.scalars(select(Person)).all()
    assert [p.full_name for p in people] == ["(sin nombre en ConsorPlus)"] * 2
    assert {p.email for p in people} == {None, "sin.nombre@example.com"}
    assert db_session.scalar(select(Phone)).e164 == "+5493515550505"
    assert report.nameless_contacts == 2
    assert report.units_without_owner_phone == 1  # 9002 only has a tenant


def test_nameless_contacts_are_deduplicated_by_phone_or_email_only(db_session) -> None:
    first = RosterContact(name="", phone="3515550505")
    same_phone = RosterContact(name="", mobile="0351 15 5550505", email="x@example.com")
    same_email = RosterContact(name="", email="X@example.com")
    other = RosterContact(name="", email="otra@example.com")
    source = FakeSource(
        {
            "7": [
                row("9001", first),
                row("9002", same_phone),
                row("9003", tenant=same_email),
                row("9004", other),
            ]
        }
    )

    sync_roster(db_session, source, ["7"])
    sync_roster(db_session, source, ["7"])  # and idempotent

    assert count(db_session, Person) == 2
    assert count(db_session, UnitPerson) == 4


def test_nameless_contact_is_not_merged_into_a_named_person(db_session) -> None:
    nameless = RosterContact(name="", email="juan@example.com")
    source = FakeSource({"7": [row("9001", JUAN), row("9002", tenant=nameless)]})

    sync_roster(db_session, source, ["7"])

    assert count(db_session, Person) == 2


# --- Data that disappears from ConsorPlus -------------------------------------------------


def test_units_no_longer_listed_are_deactivated(db_session) -> None:
    both = FakeSource({"7": [row("9001", JUAN), row("9002", ANA)]})
    sync_roster(db_session, both, ["7"])

    report = sync_roster(db_session, FakeSource({"7": [row("9001", JUAN)]}), ["7"])

    active = dict(db_session.execute(select(Unit.consorplus_unit_value, Unit.active)).all())
    assert active == {"9001": True, "9002": False}
    assert report.units_deactivated == 1

    sync_roster(db_session, both, ["7"])  # listed again: active again
    assert db_session.scalar(select(Unit).where(Unit.consorplus_unit_value == "9002")).active


def test_empty_listing_does_not_deactivate_units(db_session) -> None:
    sync_roster(db_session, FakeSource({"7": [row("9001", JUAN)]}), ["7"])

    report = sync_roster(db_session, FakeSource({"7": []}), ["7"])

    assert db_session.scalar(select(Unit)).active is True
    assert report.units_deactivated == 0
    assert db_session.scalar(select(Building)).name == "007 CONSORCIO FICTICIO"


def test_consorplus_phones_no_longer_listed_are_removed(db_session) -> None:
    sync_roster(db_session, FakeSource({"7": [row("9001", JUAN)]}), ["7"])
    juan = db_session.scalar(select(Person))
    db_session.add_all(
        [
            Phone(person_id=juan.id, e164="+5493515550901", source=DataSource.BOT_VERIFIED),
            Phone(person_id=juan.id, e164="+5493515550902", source=DataSource.MANUAL),
        ]
    )
    db_session.commit()

    report = sync_roster(db_session, FakeSource({"7": [row("9001", JUAN_NEW_PHONE)]}), ["7"])

    phones = dict(db_session.execute(select(Phone.e164, Phone.source)).all())
    assert phones == {
        "+5493515550999": DataSource.CONSORPLUS,
        "+5493515550901": DataSource.BOT_VERIFIED,
        "+5493515550902": DataSource.MANUAL,
    }
    assert report.phones_removed == 1


def test_phones_of_people_in_several_buildings_are_not_removed(db_session) -> None:
    source = FakeSource({"7": [row("9001", JUAN)], "8": [row("9101", JUAN, building="008")]})
    sync_roster(db_session, source)
    assert count(db_session, Person) == 1

    report = sync_roster(db_session, FakeSource({"7": [row("9001", JUAN_NEW_PHONE)]}), ["7"])

    assert set(db_session.scalars(select(Phone.e164))) == {"+5493515550101", "+5493515550999"}
    assert (report.phones_removed, report.people_in_several_buildings) == (0, 1)


def test_removals_are_skipped_when_the_building_fails(db_session) -> None:
    sync_roster(db_session, FakeSource({"7": [row("9001", JUAN), row("9002", ANA)]}), ["7"])
    broken = row("9" * 60, ANA)  # longer than units.consorplus_unit_value: the DB rejects it

    report = sync_roster(
        db_session, FakeSource({"7": [row("9001", JUAN_NEW_PHONE), broken]}), ["7"]
    )

    assert report.buildings_failed == 1
    assert all(db_session.scalars(select(Unit.active)))
    phones = set(db_session.scalars(select(Phone.e164)))
    assert "+5493515550101" in phones
    assert "+5493515550999" not in phones
