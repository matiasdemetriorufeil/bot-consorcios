"""The roster edited by hand (app.admin.neighbors_service). All data here is invented."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.admin import neighbors_service as svc
from app.admin import pending
from app.audit import EVENT_TYPE
from app.bot.identity import can_view_unit_finance, identify_by_phone
from app.db.models import (
    BotEvent,
    DataSource,
    Person,
    PersonRole,
    Phone,
    Provider,
    RosterSuppression,
    SuppressionKind,
    SyncJob,
    SyncKind,
    SyncRun,
    SyncStatus,
    UnitPerson,
)
from app.sync.nightly import advisory_lock
from app.sync.normalize import name_key
from app.sync.roster import sync_roster
from tests.bot import factories as f
from tests.sync.test_roster_sync import JUAN, FakeSource, row

USER = "operadora"
NEW_PHONE = "+5493515550777"


@pytest.fixture
def unit(db_session: Session):
    return f.unit(db_session, f.building(db_session, "031 RODAS FICTICIO"), "04-C")


def _events(session: Session) -> list[dict]:
    return [
        e.payload
        for e in session.scalars(select(BotEvent).where(BotEvent.event_type == EVENT_TYPE))
    ]


def test_a_person_added_by_hand_is_recognized_and_as_owner_sees_the_debt(
    db_session: Session, unit
) -> None:
    saved = svc.add_person(
        db_session, USER, unit.id, PersonRole.OWNER, full_name="  Nora   Inventada ",
        phones=["0351 15 555-0777"],
    )  # fmt: skip

    assert isinstance(saved, svc.PersonSaved) and saved.warnings == []
    who = identify_by_phone(db_session, NEW_PHONE)
    assert who.person_id == saved.person_id and who.full_name == "Nora Inventada"
    assert [(u.unit_id, u.role) for u in who.units] == [(unit.id, PersonRole.OWNER)]
    assert can_view_unit_finance(db_session, NEW_PHONE, unit.id)
    phone = db_session.scalar(select(Phone).where(Phone.e164 == NEW_PHONE))
    assert (phone.source, phone.verified, phone.needs_review, phone.conflict) == (
        DataSource.MANUAL, True, False, False,
    )  # fmt: skip
    link = db_session.get(UnitPerson, (unit.id, saved.person_id, PersonRole.OWNER))
    assert link.source == DataSource.MANUAL
    assert db_session.get(Person, saved.person_id).consorplus_name is None
    [event] = _events(db_session)
    assert event["action"] == "neighbor_added" and "Nora" not in str(event)


def test_an_existing_person_added_as_resident(db_session: Session, unit) -> None:
    other = f.unit(db_session, unit.building, "05-A")
    nora = f.person(db_session, "Nora Inventada", phone=NEW_PHONE)
    f.link(db_session, other, nora)

    saved = svc.add_person(
        db_session, USER, unit.id, PersonRole.RESIDENT, person_id=nora.id, phones=[NEW_PHONE]
    )

    assert saved == svc.PersonSaved(nora.id, [])
    roles = {(u.unit_id, u.role) for u in identify_by_phone(db_session, NEW_PHONE).units}
    assert roles == {(other.id, PersonRole.OWNER), (unit.id, PersonRole.RESIDENT)}
    assert not can_view_unit_finance(db_session, NEW_PHONE, unit.id)
    with pytest.raises(svc.NeighborError, match="ya está en esa unidad"):
        svc.add_person(db_session, USER, unit.id, PersonRole.RESIDENT, person_id=nora.id)


def test_a_number_of_another_person_changes_nothing_until_moved(db_session: Session, unit) -> None:
    owner = f.person(db_session, "Dueño Inventado", phone=NEW_PHONE)  # from ConsorPlus
    owner.consorplus_name = owner.full_name
    f.link(db_session, unit, owner)
    nora = f.person(db_session, "Nora Inventada")
    f.link(db_session, unit, nora, PersonRole.TENANT)

    taken = svc.add_phone(db_session, USER, nora.id, "351 555-0777")
    added = svc.add_person(
        db_session, USER, unit.id, PersonRole.RESIDENT, full_name="Otra", phones=[NEW_PHONE]
    )

    assert taken == added
    assert isinstance(taken, svc.PhoneTaken)
    assert (taken.person_id, taken.full_name) == (owner.id, "Dueño Inventado")
    assert taken.units == ["RODAS FICTICIO · 04-C (propietario/a)"]
    assert db_session.scalar(select(Person).where(Person.full_name == "Otra")) is None
    assert _events(db_session) == []

    svc.move_phone(db_session, USER, taken.phone_id, nora.id)

    phone = db_session.get(Phone, taken.phone_id)
    assert (phone.person_id, phone.source, phone.verified) == (nora.id, DataSource.MANUAL, True)
    [suppression] = db_session.scalars(select(RosterSuppression)).all()
    assert (suppression.kind, suppression.e164, suppression.name_key) == (
        SuppressionKind.PHONE, NEW_PHONE, name_key("Dueño Inventado"),
    )  # fmt: skip


def test_the_whatsapp_of_an_active_provider_warns(db_session: Session, unit) -> None:
    db_session.add(Provider(name="Ascensores Ficticios", whatsapp_e164=NEW_PHONE))
    nora = f.person(db_session, "Nora Inventada")
    f.link(db_session, unit, nora)

    saved = svc.add_phone(db_session, USER, nora.id, NEW_PHONE)

    assert isinstance(saved, svc.PhoneSaved)
    assert saved.warning and "Ascensores Ficticios" in saved.warning


@pytest.mark.parametrize(
    ("raw", "message"), [("555-0777", "característica"), ("12", "no es válido")]
)
def test_a_number_without_area_code_or_invalid_is_not_saved(
    db_session: Session, unit, raw: str, message: str
) -> None:
    nora = f.person(db_session, "Nora Inventada")
    f.link(db_session, unit, nora)

    with pytest.raises(svc.NeighborError, match=message):
        svc.add_phone(db_session, USER, nora.id, raw)
    with pytest.raises(svc.NeighborError, match=message):
        svc.add_person(db_session, USER, unit.id, PersonRole.TENANT, full_name="X", phones=[raw])
    assert db_session.scalars(select(Phone)).all() == []


def test_leaving_the_last_unit_deletes_phones_and_person(db_session: Session, unit) -> None:
    juan = f.person(db_session, "PEREZ JUAN", phone=NEW_PHONE)
    juan.consorplus_name = juan.full_name
    f.link(db_session, unit, juan)
    manual = svc.add_phone(db_session, USER, juan.id, "3515550888")

    assert svc.remove_from_unit(db_session, USER, unit.id, juan.id, PersonRole.OWNER)

    db_session.expire_all()
    assert db_session.get(Person, juan.id) is None
    assert db_session.scalars(select(Phone)).all() == []
    suppressions = {
        (s.kind, s.unit_id, s.role, s.e164, s.name_key)
        for s in db_session.scalars(select(RosterSuppression))
    }
    assert suppressions == {
        (SuppressionKind.LINK, unit.id, PersonRole.OWNER, None, name_key("PEREZ JUAN")),
        (
            SuppressionKind.PHONE,
            None,
            None,
            NEW_PHONE,
            name_key("PEREZ JUAN"),
        ),  # not the manual one
    }
    assert isinstance(manual, svc.PhoneSaved)
    event = _events(db_session)[-1]
    assert event["action"] == "neighbor_removed" and event["person_deleted"] is True
    assert "PEREZ" not in str(_events(db_session))


def test_change_role_of_a_manual_link_is_in_place(db_session: Session, unit) -> None:
    saved = svc.add_person(db_session, USER, unit.id, PersonRole.TENANT, full_name="Nora")

    svc.change_role(
        db_session, USER, unit.id, saved.person_id, PersonRole.TENANT, PersonRole.RESIDENT
    )

    link = db_session.get(UnitPerson, (unit.id, saved.person_id, PersonRole.RESIDENT))
    assert link.source == DataSource.MANUAL
    assert db_session.scalars(select(RosterSuppression)).all() == []


# --- refresh_building ------------------------------------------------------------------------


def _synced_building(db_session: Session) -> FakeSource:
    source = FakeSource({"7": [row("9001", JUAN)]})
    sync_roster(db_session, source, ["7"])
    return source


def test_refresh_with_the_nightly_lock_taken(db_session: Session, db_engine: Engine) -> None:
    source = _synced_building(db_session)

    busy = pytest.raises(svc.NeighborError, match="Ya se está sincronizando")
    with advisory_lock(db_engine), busy:
        svc.refresh_building(db_session, USER, "7", source=source, engine=db_engine)


def test_refresh_returns_counters_and_does_not_hide_a_failed_night(
    db_session: Session, db_engine: Engine
) -> None:
    source = _synced_building(db_session)
    db_session.query(SyncRun).delete()
    db_session.add(SyncRun(kind=SyncKind.NIGHTLY, job=SyncJob.ROSTER, status=SyncStatus.FAILED))
    db_session.commit()

    counters = svc.refresh_building(db_session, USER, "7", source=source, engine=db_engine)

    assert counters["units"] == 1 and counters["buildings_ok"] == 1
    live = db_session.scalar(select(SyncRun).where(SyncRun.kind == SyncKind.LIVE))
    assert (live.job, live.status) == (SyncJob.ROSTER, SyncStatus.OK)
    text, problem = pending.last_sync(db_session, datetime.now(UTC), "America/Argentina/Cordoba")
    assert problem and text.startswith("Falló")
    with pytest.raises(svc.NeighborError, match="No existe"):
        svc.refresh_building(db_session, USER, "999", source=source, engine=db_engine)
