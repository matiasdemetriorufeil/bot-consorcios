"""Model tests against a real Postgres test database. All data here is made up."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    BotEvent,
    Building,
    Coupon,
    DataSource,
    DebtLine,
    DebtSnapshot,
    Person,
    PersonRole,
    Phone,
    SyncKind,
    SyncRun,
    Unit,
    UnitPerson,
)
from app.db.queries import get_latest_debt

BASE_TIME = datetime(2026, 3, 10, 3, 0, tzinfo=UTC)


def make_building(session: Session, code: int = 901, *, pilot: bool = False) -> Building:
    building = Building(
        consorplus_code=code,
        name=f"{code:03d} EDIFICIO FICTICIO",
        address="Calle Falsa 123",
        pilot=pilot,
    )
    session.add(building)
    session.flush()
    return building


def make_unit(session: Session, building: Building, value: str = "1001") -> Unit:
    unit = Unit(building=building, consorplus_unit_value=value, label=f"{value} 01º A")
    session.add(unit)
    session.flush()
    return unit


def make_snapshot(
    session: Session,
    unit: Unit,
    fetched_at: datetime,
    total: str,
    source: SyncKind = SyncKind.NIGHTLY,
) -> DebtSnapshot:
    snapshot = DebtSnapshot(
        unit=unit,
        fetched_at=fetched_at,
        source=source,
        total_amount=Decimal(total),
        is_up_to_date=Decimal(total) == 0,
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def test_building_defaults(db_session: Session) -> None:
    building = make_building(db_session)
    db_session.refresh(building)

    assert building.active is True
    assert building.pilot is False
    assert building.created_at is not None
    assert building.updated_at is not None


def test_person_phone_unit_building_relationships(db_session: Session) -> None:
    building = make_building(db_session, pilot=True)
    unit = make_unit(db_session, building)
    owner = Person(full_name="Juana Pérez Ficticia", dni="00000001")
    tenant = Person(full_name="Pedro Gómez Ficticio")
    owner.phones.append(
        Phone(e164="+5493510000001", raw="351 000-0001", source=DataSource.CONSORPLUS)
    )
    db_session.add_all(
        [
            UnitPerson(
                unit=unit, person=owner, role=PersonRole.OWNER, source=DataSource.CONSORPLUS
            ),
            UnitPerson(unit=unit, person=tenant, role=PersonRole.TENANT, source=DataSource.MANUAL),
        ]
    )
    db_session.flush()
    db_session.expire_all()

    # Identify the person by phone and walk up to the building.
    phone = db_session.scalars(select(Phone).where(Phone.e164 == "+5493510000001")).one()
    assert phone.person.full_name == "Juana Pérez Ficticia"
    assert phone.verified is False
    assert phone.needs_review is False
    [link] = phone.person.units
    assert link.role is PersonRole.OWNER
    assert link.unit.building.consorplus_code == 901
    assert link.unit.building.pilot is True

    unit = db_session.get(Unit, unit.id)
    assert unit is not None
    assert {(up.person.full_name, up.role) for up in unit.people} == {
        ("Juana Pérez Ficticia", PersonRole.OWNER),
        ("Pedro Gómez Ficticio", PersonRole.TENANT),
    }
    assert [u.id for u in unit.building.units] == [unit.id]


def test_phone_e164_is_unique(db_session: Session) -> None:
    person = Person(full_name="Persona Ficticia")
    person.phones.append(Phone(e164="+5493510000002", source=DataSource.MANUAL))
    db_session.add(person)
    db_session.flush()

    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(Phone(person=person, e164="+5493510000002", source=DataSource.BOT_VERIFIED))
        db_session.flush()


def test_unit_value_is_unique_per_building(db_session: Session) -> None:
    building_a = make_building(db_session, 901)
    building_b = make_building(db_session, 902)
    make_unit(db_session, building_a, "1001")
    make_unit(db_session, building_b, "1001")  # same value in another building is fine

    with pytest.raises(IntegrityError), db_session.begin_nested():
        make_unit(db_session, building_a, "1001")


def test_building_code_is_unique(db_session: Session) -> None:
    make_building(db_session, 901)

    with pytest.raises(IntegrityError), db_session.begin_nested():
        make_building(db_session, 901)


def test_invalid_enum_value_is_rejected_by_database(db_session: Session) -> None:
    unit = make_unit(db_session, make_building(db_session))
    person = Person(full_name="Persona Ficticia")
    db_session.add(person)
    db_session.flush()

    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(
            text(
                "INSERT INTO unit_people (unit_id, person_id, role, source) "
                "VALUES (:unit_id, :person_id, 'landlord', 'manual')"
            ),
            {"unit_id": unit.id, "person_id": person.id},
        )


def test_building_with_units_cannot_be_deleted(db_session: Session) -> None:
    building = make_building(db_session)
    make_unit(db_session, building)

    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(delete(Building).where(Building.id == building.id))


def test_deleting_snapshot_deletes_its_lines(db_session: Session) -> None:
    unit = make_unit(db_session, make_building(db_session))
    snapshot = make_snapshot(db_session, unit, BASE_TIME, "1500.00")
    snapshot.lines.append(
        DebtLine(
            concept="Expensas ordinarias",
            period="02/2026",
            concept_amount=Decimal("1500.00"),
            balance_due=Decimal("1500.00"),
            accumulated=Decimal("1500.00"),
        )
    )
    db_session.flush()

    db_session.execute(delete(DebtSnapshot).where(DebtSnapshot.id == snapshot.id))

    assert db_session.scalars(select(DebtLine)).all() == []


def test_get_latest_debt_returns_most_recent_snapshot_with_lines(db_session: Session) -> None:
    building = make_building(db_session)
    unit = make_unit(db_session, building, "1001")
    other_unit = make_unit(db_session, building, "1002")

    make_snapshot(db_session, unit, BASE_TIME - timedelta(days=2), "3000.00")
    latest = make_snapshot(db_session, unit, BASE_TIME, "1234.56", source=SyncKind.LIVE)
    latest.lines.extend(
        [
            DebtLine(
                concept="Expensas ordinarias",
                period="01/2026",
                concept_amount=Decimal("1000.00"),
                balance_due=Decimal("234.56"),
                accumulated=Decimal("234.56"),
            ),
            DebtLine(
                concept="Expensas ordinarias",
                period="02/2026",
                concept_amount=Decimal("1000.00"),
                balance_due=Decimal("1000.00"),
                accumulated=Decimal("1234.56"),
            ),
        ]
    )
    # Inserted last but fetched earlier: must not win.
    make_snapshot(db_session, unit, BASE_TIME - timedelta(days=1), "2000.00")
    # A newer snapshot of a different unit must be ignored.
    make_snapshot(db_session, other_unit, BASE_TIME + timedelta(days=1), "0.00")
    db_session.flush()
    db_session.expire_all()

    result = get_latest_debt(db_session, unit.id)

    assert result is not None
    assert result.id == latest.id
    assert result.source is SyncKind.LIVE
    assert result.total_amount == Decimal("1234.56")
    assert result.is_up_to_date is False
    assert [line.period for line in result.lines] == ["01/2026", "02/2026"]
    assert result.lines[-1].accumulated == Decimal("1234.56")


def test_get_latest_debt_breaks_ties_by_id(db_session: Session) -> None:
    unit = make_unit(db_session, make_building(db_session))
    make_snapshot(db_session, unit, BASE_TIME, "10.00")
    second = make_snapshot(db_session, unit, BASE_TIME, "20.00")

    result = get_latest_debt(db_session, unit.id)

    assert result is not None
    assert result.id == second.id


def test_get_latest_debt_returns_none_without_snapshots(db_session: Session) -> None:
    unit = make_unit(db_session, make_building(db_session))

    assert get_latest_debt(db_session, unit.id) is None


def test_coupon_belongs_to_unit(db_session: Session) -> None:
    unit = make_unit(db_session, make_building(db_session))
    unit.coupons.append(
        Coupon(
            period="03/2026",
            coupon_id="0000000000000001",
            amount_1=Decimal("1000.00"),
            due_date_1=date(2026, 3, 10),
            amount_2=Decimal("1050.00"),
            due_date_2=date(2026, 3, 20),
        )
    )
    db_session.flush()
    db_session.expire_all()

    coupon = db_session.scalars(select(Coupon)).one()
    assert coupon.unit.id == unit.id
    assert coupon.detail_url is None
    assert coupon.fetched_at is not None


def make_coupon(unit: Unit, period: str, coupon_id: str, amount: str = "1000.00") -> Coupon:
    return Coupon(
        unit=unit,
        period=period,
        coupon_id=coupon_id,
        amount_1=Decimal(amount),
        due_date_1=date(2026, 3, 10),
    )


def test_coupon_is_unique_per_unit_period_and_id(db_session: Session) -> None:
    building = make_building(db_session)
    unit = make_unit(db_session, building, "1001")
    other_unit = make_unit(db_session, building, "1002")
    db_session.add_all(
        [
            make_coupon(unit, "03/2026", "0000000000000001"),
            make_coupon(unit, "03/2026", "0000000000000002"),  # another coupon, same period
            make_coupon(unit, "04/2026", "0000000000000001"),  # same id, another period
            make_coupon(other_unit, "03/2026", "0000000000000001"),  # another unit
        ]
    )
    db_session.flush()

    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(make_coupon(unit, "03/2026", "0000000000000001"))
        db_session.flush()


def test_coupon_upsert_on_unique_key_updates_existing_row(db_session: Session) -> None:
    unit = make_unit(db_session, make_building(db_session))
    values = {
        "unit_id": unit.id,
        "period": "03/2026",
        "coupon_id": "0000000000000001",
        "amount_1": Decimal("1000.00"),
        "due_date_1": date(2026, 3, 10),
    }
    for amount in ("1000.00", "1100.00"):
        stmt = insert(Coupon).values({**values, "amount_1": Decimal(amount)})
        stmt = stmt.on_conflict_do_update(
            index_elements=["unit_id", "period", "coupon_id"],
            set_={"amount_1": stmt.excluded.amount_1, "fetched_at": func.now()},
        )
        db_session.execute(stmt)

    coupon = db_session.scalars(select(Coupon)).one()
    assert coupon.amount_1 == Decimal("1100.00")


def test_sync_run_starts_without_status(db_session: Session) -> None:
    run = SyncRun(kind=SyncKind.NIGHTLY)
    db_session.add(run)
    db_session.flush()
    db_session.refresh(run)

    assert run.status is None
    assert run.finished_at is None
    assert (run.units_ok, run.units_failed) == (0, 0)


def test_bot_event_payload_roundtrips_as_jsonb(db_session: Session) -> None:
    db_session.add_all(
        [
            BotEvent(
                conversation_id=42,
                phone_e164="+5493510000003",
                event_type="debt_requested",
                payload={"unit_ids": [1, 2], "ok": True},
            ),
            BotEvent(event_type="handoff"),
        ]
    )
    db_session.flush()

    matching = db_session.scalars(
        select(BotEvent).where(BotEvent.payload["ok"].as_boolean().is_(True))
    ).all()
    assert [event.event_type for event in matching] == ["debt_requested"]
    assert matching[0].payload == {"unit_ids": [1, 2], "ok": True}

    handoff = db_session.scalars(select(BotEvent).where(BotEvent.event_type == "handoff")).one()
    assert handoff.payload == {}
