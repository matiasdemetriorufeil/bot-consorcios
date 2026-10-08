"""The rules of a claim (app.claims.service), against the Postgres test database. All data is
invented (fictitious buildings and providers, phones 351 555-01xx)."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.claims.service import (
    TRANSITIONS,
    ClaimProblem,
    CreateResult,
    InvalidTransition,
    Reporter,
    add_note,
    change_provider,
    claims_for_phone,
    close_claim,
    create_claim,
    mark_acknowledged,
    mark_sent,
)
from app.db.models import (
    BotEvent,
    Building,
    Claim,
    ClaimActor,
    ClaimCategory,
    ClaimEvent,
    ClaimEventKind,
    ClaimScope,
    ClaimSource,
    ClaimStatus,
    Provider,
    Unit,
)
from tests.bot import factories as f
from tests.claims import factories as cf

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
WA_1 = "+5493515550101"
WA_2 = "+5493515550102"
WA_3 = "+5493515550103"
ANA = Reporter(name="Ana Inventada", phone_e164=WA_1)
BETO = Reporter(name="Beto Ficticio", phone_e164=WA_2)


@dataclass
class World:
    building: Building
    other: Building
    unit: Unit
    unit_b: Unit
    other_unit: Unit
    lift: ClaimCategory  # whole building, urgent, with a provider
    damp: ClaimCategory  # one unit, the studio
    gate: ClaimCategory  # whole building, disabled
    noise: ClaimCategory  # whole building, not in this building's table
    lifts: Provider


@pytest.fixture
def w(db_session: Session) -> World:
    s = db_session
    building = f.building(s, "001 TORRE INVENTADA")
    other = f.building(s, "002 OTRA FICTICIA")
    unit, unit_b = f.unit(s, building, "01-A"), f.unit(s, building, "02-B")
    other_unit = f.unit(s, other, "05-C")
    lift = cf.category(s, "Ascensor inventado", list_title="Ascensor")
    lift.urgent = True
    damp = cf.category(s, "Humedad inventada", list_title="Humedad", scope=ClaimScope.UNIT)
    gate = cf.category(s, "Portón inventado", list_title="Portón")
    noise = cf.category(s, "Ruido inventado", list_title="Ruido")
    lifts = cf.provider(s, "Ascensores Ficticios SRL", WA_3)
    cf.assign(s, building.id, lift, lifts)
    cf.assign(s, building.id, damp)
    cf.assign(s, building.id, gate, enabled=False)
    cf.assign(s, other.id, noise)
    s.commit()
    return World(building, other, unit, unit_b, other_unit, lift, damp, gate, noise, lifts)


def _create(
    session: Session,
    building: Building,
    category: ClaimCategory,
    reporter: Reporter = ANA,
    *,
    unit: Unit | None = None,
    now: datetime = NOW,
    source: ClaimSource = ClaimSource.PANEL,
    **kwargs: Any,
) -> CreateResult:
    return create_claim(
        session,
        building_id=building.id,
        category_id=category.id,
        unit_id=unit.id if unit else None,
        description=kwargs.pop("description", "No anda desde la mañana"),
        reporter=reporter,
        source=source,
        user=kwargs.pop("user", "marta"),
        now=now,
        **kwargs,
    )


def _kinds(session: Session, claim: Claim) -> list[str]:
    session.expire_all()
    rows = session.scalars(
        select(ClaimEvent).where(ClaimEvent.claim_id == claim.id).order_by(ClaimEvent.id)
    )
    return [str(e.kind) for e in rows]


def _audit(session: Session) -> list[str]:
    rows = session.scalars(
        select(BotEvent).where(BotEvent.event_type == "admin_action").order_by(BotEvent.id)
    )
    return [e.payload["action"] for e in rows]


# --- Creating -------------------------------------------------------------------------------


def test_with_a_provider_it_waits_to_be_sent(db_session: Session, w: World) -> None:
    result = _create(db_session, w.building, w.lift, unit=w.unit)
    claim = result.claim
    assert not result.repeated
    assert claim.status == ClaimStatus.PENDING_SEND and claim.provider_id == w.lifts.id
    assert claim.scope == ClaimScope.BUILDING and claim.urgent
    assert claim.unit_id is None  # a problem of the whole building
    assert claim.reporter_unit_id == w.unit.id  # ...but who reported keeps their unit
    assert (claim.reporter_name, claim.reporter_phone_e164) == ("Ana Inventada", WA_1)
    assert claim.created_by_user == "marta" and claim.status_at == NOW
    assert _kinds(db_session, claim) == ["created"]
    assert _audit(db_session) == ["claim_created"]


def test_without_a_provider_the_studio_attends_it(db_session: Session, w: World) -> None:
    claim = _create(db_session, w.building, w.damp, unit=w.unit).claim
    assert claim.status == ClaimStatus.STUDIO and claim.provider_id is None
    assert claim.unit_id == w.unit.id and claim.scope == ClaimScope.UNIT


def test_a_deactivated_provider_is_not_copied(db_session: Session, w: World) -> None:
    w.lifts.active = False
    db_session.flush()
    claim = _create(db_session, w.building, w.lift).claim
    assert claim.status == ClaimStatus.STUDIO and claim.provider_id is None


def test_from_the_bot_there_is_no_admin_action(db_session: Session, w: World) -> None:
    claim = _create(db_session, w.building, w.lift, source=ClaimSource.BOT, user=None).claim
    assert claim.created_by_user is None
    [event] = db_session.scalars(select(ClaimEvent).where(ClaimEvent.claim_id == claim.id))
    assert event.actor == ClaimActor.BOT
    assert _audit(db_session) == []


def test_scope_and_urgent_stay_when_the_kind_changes(db_session: Session, w: World) -> None:
    claim = _create(db_session, w.building, w.lift).claim
    w.lift.urgent = False
    w.lift.scope = ClaimScope.UNIT
    db_session.commit()
    db_session.refresh(claim)
    assert claim.urgent and claim.scope == ClaimScope.BUILDING


@pytest.mark.parametrize("which", ["gate", "noise"])
def test_a_disabled_kind_or_one_of_another_building_is_refused(
    db_session: Session, w: World, which: str
) -> None:
    with pytest.raises(ClaimProblem, match="no se puede reclamar en este edificio"):
        _create(db_session, w.building, getattr(w, which))


def test_an_inactive_kind_is_refused(db_session: Session, w: World) -> None:
    w.lift.active = False
    db_session.flush()
    with pytest.raises(ClaimProblem, match="no se puede reclamar"):
        _create(db_session, w.building, w.lift)


def test_a_unit_of_another_building_is_refused(db_session: Session, w: World) -> None:
    with pytest.raises(ClaimProblem, match="no es de este edificio"):
        _create(db_session, w.building, w.damp, unit=w.other_unit)
    reporter = Reporter(name="Ana Inventada", unit_id=w.other_unit.id)
    with pytest.raises(ClaimProblem, match="no es de este edificio"):
        _create(db_session, w.building, w.lift, reporter)


def test_a_kind_of_one_unit_needs_the_unit(db_session: Session, w: World) -> None:
    with pytest.raises(ClaimProblem, match="Elegí la unidad"):
        _create(db_session, w.building, w.damp)


def test_description_and_reporter_are_needed(db_session: Session, w: World) -> None:
    with pytest.raises(ClaimProblem, match="Contá cuál es el problema"):
        _create(db_session, w.building, w.lift, description="  ")
    with pytest.raises(ClaimProblem, match="quién reclamó"):
        _create(db_session, w.building, w.lift, Reporter(name=" "))


# --- Repeated claims ------------------------------------------------------------------------


def test_the_whole_building_merges_repeated_claims(db_session: Session, w: World) -> None:
    first = _create(db_session, w.building, w.lift, ANA).claim
    second = _create(db_session, w.building, w.lift, BETO, unit=w.unit_b, description="Trabado")

    assert second.repeated and not second.already_reporter
    assert second.claim.id == first.id
    assert db_session.scalars(select(Claim)).all() == [first]
    [joined] = first.reporters
    assert (joined.name, joined.phone_e164, joined.unit_id) == (
        "Beto Ficticio",
        WA_2,
        w.unit_b.id,
    )
    assert _kinds(db_session, first) == ["created", "joined"]
    joined_event = db_session.scalars(
        select(ClaimEvent).where(ClaimEvent.kind == ClaimEventKind.JOINED)
    ).one()
    assert joined_event.text == "Beto Ficticio: Trabado"
    assert _audit(db_session) == ["claim_created", "claim_joined"]


def test_a_neighbor_already_in_is_not_added_twice(db_session: Session, w: World) -> None:
    first = _create(db_session, w.building, w.lift, ANA).claim
    _create(db_session, w.building, w.lift, BETO)

    again_first = _create(db_session, w.building, w.lift, ANA)
    again_joined = _create(db_session, w.building, w.lift, BETO)

    assert again_first.already_reporter and again_joined.already_reporter
    assert len(first.reporters) == 1
    assert _kinds(db_session, first) == ["created", "joined"]


def test_without_phone_the_same_name_is_the_same_neighbor(db_session: Session, w: World) -> None:
    first = _create(db_session, w.building, w.lift, Reporter(name="Carla Inventada")).claim
    again = _create(db_session, w.building, w.lift, Reporter(name=" carla inventada "))
    assert again.already_reporter and again.claim.id == first.id


def test_a_closed_claim_is_not_joined(db_session: Session, w: World) -> None:
    first = _create(db_session, w.building, w.lift, ANA).claim
    close_claim(db_session, first, ClaimStatus.SOLVED, "Arreglado", actor=ClaimActor.PANEL)
    second = _create(db_session, w.building, w.lift, BETO)
    assert not second.repeated and second.claim.id != first.id


def test_one_unit_never_merges(db_session: Session, w: World) -> None:
    first = _create(db_session, w.building, w.damp, ANA, unit=w.unit).claim
    second = _create(db_session, w.building, w.damp, ANA, unit=w.unit)
    assert not second.repeated and second.claim.id != first.id


def test_numbers_start_at_1001_and_a_repeated_one_takes_none(db_session: Session, w: World) -> None:
    start = db_session.scalar(
        text("SELECT start_value FROM pg_sequences WHERE sequencename = 'claims_number_seq'")
    )
    assert start == 1001
    first = _create(db_session, w.building, w.lift, ANA).claim
    _create(db_session, w.building, w.lift, BETO)  # joins: no number
    _create(db_session, w.building, w.lift, ANA)  # already in: no number
    second = _create(db_session, w.building, w.damp, ANA, unit=w.unit).claim
    third = _create(db_session, w.building, w.damp, BETO, unit=w.unit_b).claim
    assert first.number >= 1001
    assert [second.number, third.number] == [first.number + 1, first.number + 2]


# --- The previous claim ---------------------------------------------------------------------


def _closed(session: Session, w: World, category: ClaimCategory, days_ago: int, **kw: Any) -> Claim:
    when = NOW - timedelta(days=days_ago)
    claim = _create(session, w.building, category, now=when - timedelta(hours=1), **kw).claim
    close_claim(session, claim, ClaimStatus.SOLVED, "Arreglado", actor=ClaimActor.PANEL, now=when)
    return claim


def test_the_previous_claim_of_the_same_neighbor(db_session: Session, w: World) -> None:
    old = _closed(db_session, w, w.lift, 40)
    recent = _closed(db_session, w, w.lift, 10)
    claim = _create(db_session, w.building, w.lift, ANA).claim
    assert claim.previous_claim_id == recent.id != old.id


def test_no_previous_claim_after_30_days(db_session: Session, w: World) -> None:
    _closed(db_session, w, w.lift, 31)
    assert _create(db_session, w.building, w.lift, ANA).claim.previous_claim_id is None


def test_no_previous_claim_of_another_kind_or_neighbor(db_session: Session, w: World) -> None:
    _closed(db_session, w, w.damp, 5, unit=w.unit)
    _closed(db_session, w, w.lift, 5, reporter=BETO)
    assert _create(db_session, w.building, w.lift, ANA).claim.previous_claim_id is None


def test_the_previous_claim_of_a_neighbor_who_joined(db_session: Session, w: World) -> None:
    old = _create(db_session, w.building, w.lift, ANA, now=NOW - timedelta(days=6)).claim
    _create(db_session, w.building, w.lift, BETO, now=NOW - timedelta(days=6))
    close_claim(
        db_session,
        old,
        ClaimStatus.SOLVED,
        "Listo",
        actor=ClaimActor.PANEL,
        now=NOW - timedelta(days=5),
    )
    assert _create(db_session, w.building, w.lift, BETO).claim.previous_claim_id == old.id


def test_one_unit_the_previous_claim_of_the_unit(db_session: Session, w: World) -> None:
    old = _closed(db_session, w, w.damp, 3, unit=w.unit, reporter=BETO)
    claim = _create(db_session, w.building, w.damp, ANA, unit=w.unit).claim
    assert claim.previous_claim_id == old.id
    other = _create(db_session, w.building, w.damp, BETO, unit=w.unit_b, description="Otra")
    assert other.claim.previous_claim_id == old.id  # the same neighbor, another unit
    carla = Reporter(name="Carla Inventada")  # neither the neighbor nor the unit
    assert (
        _create(db_session, w.building, w.damp, carla, unit=w.unit_b).claim.previous_claim_id
        is None
    )


# --- Moving ---------------------------------------------------------------------------------


def test_the_full_way_to_solved(db_session: Session, w: World) -> None:
    claim = _create(db_session, w.building, w.lift).claim
    mark_sent(db_session, claim, now=NOW + timedelta(minutes=1))
    mark_acknowledged(db_session, claim, now=NOW + timedelta(minutes=5))
    close_claim(
        db_session,
        claim,
        ClaimStatus.SOLVED,
        "Cambiaron el motor",
        actor=ClaimActor.PANEL,
        user="marta",
        now=NOW + timedelta(hours=3),
    )
    assert claim.status == ClaimStatus.SOLVED
    assert claim.sent_at == NOW + timedelta(minutes=1)
    assert claim.acknowledged_at == NOW + timedelta(minutes=5)
    assert claim.closed_at == claim.status_at == NOW + timedelta(hours=3)
    assert claim.close_reason == "Cambiaron el motor"
    assert _kinds(db_session, claim) == ["created", "sent", "acknowledged", "solved"]
    assert _audit(db_session) == ["claim_created", "claim_solved"]


def _at(session: Session, w: World, status: ClaimStatus) -> Claim:
    """A claim in that status, along allowed moves."""
    claim = _create(session, w.building, w.lift, description=f"en {status}").claim
    if status == ClaimStatus.STUDIO:
        change_provider(session, claim, None, actor=ClaimActor.PANEL, user="marta")
    elif status in (ClaimStatus.SENT, ClaimStatus.ACKNOWLEDGED):
        mark_sent(session, claim)
        if status == ClaimStatus.ACKNOWLEDGED:
            mark_acknowledged(session, claim)
    elif status in (ClaimStatus.SOLVED, ClaimStatus.CANCELLED):
        close_claim(session, claim, status, "Motivo", actor=ClaimActor.PANEL)
    assert claim.status == status
    return claim


def test_the_transition_table() -> None:
    assert TRANSITIONS[ClaimStatus.SOLVED] == frozenset()
    assert TRANSITIONS[ClaimStatus.CANCELLED] == frozenset()
    assert ClaimStatus.ACKNOWLEDGED not in TRANSITIONS[ClaimStatus.PENDING_SEND]
    assert ClaimStatus.SENT not in TRANSITIONS[ClaimStatus.STUDIO]


@pytest.mark.parametrize(
    ("status", "move"),
    [
        (ClaimStatus.PENDING_SEND, "acknowledge"),
        (ClaimStatus.STUDIO, "send"),
        (ClaimStatus.STUDIO, "acknowledge"),
        (ClaimStatus.SENT, "send"),
        (ClaimStatus.ACKNOWLEDGED, "send"),
        (ClaimStatus.ACKNOWLEDGED, "acknowledge"),
    ],
)
def test_forbidden_moves(db_session: Session, w: World, status: ClaimStatus, move: str) -> None:
    claim = _at(db_session, w, status)
    action = mark_sent if move == "send" else mark_acknowledged
    with pytest.raises(InvalidTransition):
        action(db_session, claim)
    assert claim.status == status


@pytest.mark.parametrize("status", [ClaimStatus.SOLVED, ClaimStatus.CANCELLED])
def test_a_closed_claim_never_reopens(db_session: Session, w: World, status: ClaimStatus) -> None:
    claim = _at(db_session, w, status)
    for attempt in (
        lambda: mark_sent(db_session, claim),
        lambda: mark_acknowledged(db_session, claim),
        lambda: close_claim(db_session, claim, ClaimStatus.SOLVED, "x", actor=ClaimActor.PANEL),
        lambda: close_claim(db_session, claim, ClaimStatus.CANCELLED, "x", actor=ClaimActor.PANEL),
        lambda: change_provider(db_session, claim, None, actor=ClaimActor.PANEL),
        lambda: change_provider(db_session, claim, w.lifts.id, actor=ClaimActor.PANEL),
    ):
        with pytest.raises(InvalidTransition, match="ya está cerrado"):
            attempt()
    assert claim.status == status


def test_closing_needs_a_closed_status_and_a_reason(db_session: Session, w: World) -> None:
    claim = _create(db_session, w.building, w.lift).claim
    with pytest.raises(InvalidTransition):
        close_claim(db_session, claim, ClaimStatus.SENT, "x", actor=ClaimActor.PANEL)
    with pytest.raises(ClaimProblem, match="motivo"):
        close_claim(db_session, claim, ClaimStatus.CANCELLED, "  ", actor=ClaimActor.PANEL)
    assert claim.status == ClaimStatus.PENDING_SEND


@pytest.mark.parametrize(
    "status", [ClaimStatus.PENDING_SEND, ClaimStatus.SENT, ClaimStatus.ACKNOWLEDGED]
)
def test_any_open_claim_can_be_closed_by_hand(
    db_session: Session, w: World, status: ClaimStatus
) -> None:
    claim = _at(db_session, w, status)
    close_claim(
        db_session, claim, ClaimStatus.SOLVED, "Lo resolvió el encargado", actor=ClaimActor.PANEL
    )
    assert claim.status == ClaimStatus.SOLVED


# --- Who attends it -------------------------------------------------------------------------


def test_change_provider_moves_between_studio_and_pending_send(
    db_session: Session, w: World
) -> None:
    claim = _create(db_session, w.building, w.lift).claim
    mark_sent(db_session, claim)

    change_provider(db_session, claim, None, actor=ClaimActor.PANEL, user="marta", now=NOW)
    assert claim.status == ClaimStatus.STUDIO and claim.provider_id is None
    assert claim.sent_at is None

    plumber = cf.provider(db_session, "Plomería Inventada")
    change_provider(db_session, claim, plumber.id, actor=ClaimActor.PANEL, user="marta")
    assert claim.status == ClaimStatus.PENDING_SEND and claim.provider_id == plumber.id

    change_provider(db_session, claim, w.lifts.id, actor=ClaimActor.PANEL, user="marta")
    assert claim.status == ClaimStatus.PENDING_SEND and claim.provider_id == w.lifts.id

    events = db_session.scalars(
        select(ClaimEvent)
        .where(ClaimEvent.kind == ClaimEventKind.PROVIDER_CHANGED)
        .order_by(ClaimEvent.id)
    ).all()
    assert [e.text for e in events] == [None, "Plomería Inventada", "Ascensores Ficticios SRL"]
    assert _audit(db_session).count("claim_provider_changed") == 3


def test_change_provider_refuses_the_same_or_an_inactive_one(db_session: Session, w: World) -> None:
    claim = _create(db_session, w.building, w.lift).claim
    with pytest.raises(ClaimProblem, match="No cambió"):
        change_provider(db_session, claim, w.lifts.id, actor=ClaimActor.PANEL)
    old = cf.provider(db_session, "Dado de Baja Ficticio", active=False)
    with pytest.raises(ClaimProblem, match="proveedor activo"):
        change_provider(db_session, claim, old.id, actor=ClaimActor.PANEL)


def test_notes_also_on_closed_claims(db_session: Session, w: World) -> None:
    claim = _create(db_session, w.building, w.lift).claim
    close_claim(db_session, claim, ClaimStatus.CANCELLED, "Repetido", actor=ClaimActor.PANEL)
    add_note(db_session, claim, "Lo avisé por teléfono", actor=ClaimActor.PANEL, user="marta")
    assert _kinds(db_session, claim)[-1] == "note"
    with pytest.raises(ClaimProblem, match="Escribí la nota"):
        add_note(db_session, claim, " ", actor=ClaimActor.PANEL)
    assert _audit(db_session)[-1] == "claim_note_added"


def test_claims_of_a_phone(db_session: Session, w: World) -> None:
    first = _create(db_session, w.building, w.lift, ANA).claim
    _create(db_session, w.building, w.lift, BETO)  # Beto joins
    own = _create(db_session, w.building, w.damp, BETO, unit=w.unit_b).claim
    # The newest first (same time: the last created).
    assert [c.id for c in claims_for_phone(db_session, WA_2)] == [own.id, first.id]
    assert claims_for_phone(db_session, "+5493515550199") == []
