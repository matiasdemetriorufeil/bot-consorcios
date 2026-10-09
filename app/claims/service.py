"""The rules of a claim. The ONLY way to create or change one: the panel uses it now and the
bot will (step 8.3). Nothing here sends anything to anybody: notifying the provider and the
neighbor comes in step 8.5.

- create_claim: only for a kind of problem enabled in that building. A kind of the whole
  building with an open claim of the same kind there is not created twice: the neighbor joins
  the open one (ClaimReporter). A kind of one unit needs the unit and is never merged.
- The status moves only along TRANSITIONS; a closed claim (solved, cancelled) never reopens.
- Every change leaves a ClaimEvent (the history the panel shows) and, when it comes from the
  panel, the usual admin_action in bot_events (ids and field names, never texts).

Sending the claim to the provider and telling the neighbors is app.claims.notify; the
provider's buttons are read by app.claims.provider_flow. Both change the claim only here.

Changes are flushed, never committed: the caller commits (one transaction per action).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.audit import log_admin_action
from app.db.models import (
    CLOSED_CLAIM_STATUSES,
    OPEN_CLAIM_STATUSES,
    Building,
    BuildingClaimCategory,
    Claim,
    ClaimActor,
    ClaimAttention,
    ClaimCategory,
    ClaimEvent,
    ClaimEventKind,
    ClaimReporter,
    ClaimScope,
    ClaimSource,
    ClaimStatus,
    Provider,
    Unit,
)

# A claim of the same kind closed this recently is linked as the previous one.
PREVIOUS_WINDOW = timedelta(days=30)

S = ClaimStatus
# Every allowed move. Back to pending_send / studio only through change_provider.
TRANSITIONS: dict[ClaimStatus, frozenset[ClaimStatus]] = {
    S.PENDING_SEND: frozenset({S.SENT, S.STUDIO, S.SOLVED, S.CANCELLED}),
    S.SENT: frozenset({S.ACKNOWLEDGED, S.PENDING_SEND, S.STUDIO, S.SOLVED, S.CANCELLED}),
    S.ACKNOWLEDGED: frozenset({S.PENDING_SEND, S.STUDIO, S.SOLVED, S.CANCELLED}),
    S.STUDIO: frozenset({S.PENDING_SEND, S.SOLVED, S.CANCELLED}),
    S.SOLVED: frozenset(),
    S.CANCELLED: frozenset(),
}


class ClaimProblem(ValueError):
    """Why the claim cannot be created or changed, in Spanish (shown as is)."""


class InvalidTransition(ClaimProblem):
    pass


@dataclass(frozen=True)
class Reporter:
    """Who reports: a person of the roster (person_id) or just a name and a phone."""

    name: str | None
    phone_e164: str | None = None
    person_id: int | None = None
    unit_id: int | None = None


@dataclass(frozen=True)
class CreateResult:
    claim: Claim
    repeated: bool = False  # it joined an open claim instead of creating one
    already_reporter: bool = False  # ...where that neighbor already was


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _actor(source: ClaimSource) -> ClaimActor:
    return ClaimActor.BOT if source == ClaimSource.BOT else ClaimActor.PANEL


def _event(
    session: Session,
    claim: Claim,
    kind: ClaimEventKind,
    actor: ClaimActor,
    user: str | None,
    text: str | None = None,
    now: datetime | None = None,
    wa_message_id: int | None = None,
    provider_id: int | None = None,
) -> None:
    session.add(
        ClaimEvent(
            claim_id=claim.id,
            kind=kind,
            actor=actor,
            panel_user=user if actor == ClaimActor.PANEL else None,
            text=text,
            wa_message_id=wa_message_id,
            provider_id=provider_id,
            created_at=_now(now),
        )
    )


def record_event(
    session: Session,
    claim: Claim,
    kind: ClaimEventKind,
    *,
    actor: ClaimActor = ClaimActor.SYSTEM,
    text: str | None = None,
    wa_message_id: int | None = None,
    provider_id: int | None = None,
    now: datetime | None = None,
) -> None:
    """A line of the history that changes nothing else (a WhatsApp notice, what the provider
    wrote, a notice that could not go out)."""
    _event(session, claim, kind, actor, None, text, now, wa_message_id, provider_id)
    session.flush()


def schedule_send(
    session: Session, claim: Claim, at: datetime, text: str, *, now: datetime
) -> None:
    """Out of the providers' hours: it goes at `at` (app.claims.jobs sends it)."""
    claim.send_after = at
    _event(
        session, claim, ClaimEventKind.SCHEDULED, ClaimActor.SYSTEM, None, text, now,
        provider_id=claim.provider_id,
    )  # fmt: skip
    session.flush()


def mark_reminded(
    session: Session, claim: Claim, text: str, *, wa_message_id: int | None, now: datetime
) -> None:
    """The provider got the reminder of an unconfirmed claim (once per send)."""
    claim.reminded_at = now
    _event(
        session, claim, ClaimEventKind.REMINDED, ClaimActor.SYSTEM, None, text, now,
        wa_message_id, claim.provider_id,
    )  # fmt: skip
    session.flush()


def raise_attention(
    session: Session, claim: Claim, attention: ClaimAttention, text: str, *, now: datetime
) -> bool:
    """The panel is told someone should look at it (once: False if it already was)."""
    if claim.attention == attention:
        return False
    claim.attention = attention
    _event(session, claim, ClaimEventKind.ALERT, ClaimActor.SYSTEM, None, text, now)
    session.flush()
    return True


def _audit(
    session: Session, actor: ClaimActor, user: str | None, action: str, claim: Claim, **payload
) -> None:
    if actor == ClaimActor.PANEL:
        log_admin_action(
            session, user or "?", action, claim_id=claim.id, claim_number=claim.number, **payload
        )


def _clean(text: str | None) -> str:
    return (text or "").strip()


def _same_person(reporter: Reporter, person_id: int | None, phone: str | None, name: str) -> bool:
    if reporter.person_id is not None and reporter.person_id == person_id:
        return True
    if reporter.phone_e164 and reporter.phone_e164 == phone:
        return True
    if reporter.person_id is None and not reporter.phone_e164:
        return _clean(reporter.name).casefold() == _clean(name).casefold()
    return False


def _already_in(claim: Claim, reporter: Reporter) -> bool:
    if _same_person(
        reporter, claim.reporter_person_id, claim.reporter_phone_e164, claim.reporter_name or ""
    ):
        return True
    return any(_same_person(reporter, r.person_id, r.phone_e164, r.name) for r in claim.reporters)


def _reporter_condition(reporter: Reporter) -> list:
    """Claims where this neighbor reported first or joined."""
    joined = select(ClaimReporter.claim_id)
    conditions = []
    if reporter.person_id is not None:
        conditions.append(Claim.reporter_person_id == reporter.person_id)
        conditions.append(Claim.id.in_(joined.where(ClaimReporter.person_id == reporter.person_id)))
    if reporter.phone_e164:
        conditions.append(Claim.reporter_phone_e164 == reporter.phone_e164)
        conditions.append(
            Claim.id.in_(joined.where(ClaimReporter.phone_e164 == reporter.phone_e164))
        )
    return conditions


def _previous_claim(
    session: Session,
    building_id: int,
    category: ClaimCategory,
    unit: Unit | None,
    reporter: Reporter,
    now: datetime,
) -> Claim | None:
    """The latest claim of the same kind closed in the last 30 days by the same neighbor (or,
    for a kind of one unit, of the same unit)."""
    conditions = _reporter_condition(reporter)
    if category.scope == ClaimScope.UNIT and unit is not None:
        conditions.append(Claim.unit_id == unit.id)
    if not conditions:
        return None
    return session.scalars(
        select(Claim)
        .where(
            Claim.building_id == building_id,
            Claim.category_id == category.id,
            Claim.status.in_(CLOSED_CLAIM_STATUSES),
            Claim.closed_at >= now - PREVIOUS_WINDOW,
            or_(*conditions),
        )
        .order_by(Claim.closed_at.desc(), Claim.id.desc())
        .limit(1)
    ).first()


def _own_closed_claim(session: Session, claim_id: int, reporter: Reporter) -> Claim | None:
    conditions = _reporter_condition(reporter)
    if not conditions:
        return None
    return session.scalars(
        select(Claim).where(
            Claim.id == claim_id, Claim.status.in_(CLOSED_CLAIM_STATUSES), or_(*conditions)
        )
    ).first()


def _unit_of(session: Session, unit_id: int | None, building_id: int) -> Unit | None:
    if unit_id is None:
        return None
    unit = session.get(Unit, unit_id)
    if unit is None or unit.building_id != building_id or not unit.active:
        raise ClaimProblem("Esa unidad no es de este edificio.")
    return unit


# --- Creating -------------------------------------------------------------------------------


def create_claim(
    session: Session,
    *,
    building_id: int,
    category_id: int,
    unit_id: int | None,
    description: str,
    reporter: Reporter,
    source: ClaimSource,
    user: str | None = None,
    wa_conversation_id: int | None = None,
    follow_up_answer: str | None = None,
    previous_claim_id: int | None = None,
    now: datetime | None = None,
) -> CreateResult:
    """A new claim, or the open one of the same kind of the whole building that the neighbor
    joins (CreateResult.repeated). previous_claim_id: the claim it follows ("Registrar reclamo"
    from the notice of a solved one), kept only if it is a closed claim of this neighbor.
    Raises ClaimProblem."""
    now = _now(now)
    actor = _actor(source)
    description = _clean(description)
    if not description:
        raise ClaimProblem("Contá cuál es el problema.")
    name = _clean(reporter.name)
    if not name and not reporter.phone_e164:
        raise ClaimProblem("Indicá quién reclamó.")
    building = session.get(Building, building_id)
    if building is None:
        raise ClaimProblem("Ese edificio no existe.")
    # Locked: two equal claims at the same time are created one after the other (the second
    # one joins the first).
    assignment = session.scalars(
        select(BuildingClaimCategory)
        .where(
            BuildingClaimCategory.building_id == building_id,
            BuildingClaimCategory.category_id == category_id,
        )
        .with_for_update()
    ).first()
    category = session.get(ClaimCategory, category_id)
    if assignment is None or not assignment.enabled or category is None or not category.active:
        raise ClaimProblem("Ese problema no se puede reclamar en este edificio.")
    unit = _unit_of(session, unit_id, building_id)
    if category.scope == ClaimScope.UNIT and unit is None:
        raise ClaimProblem("Elegí la unidad: este problema es de una unidad.")
    reporter_unit = _unit_of(session, reporter.unit_id, building_id) or unit
    shown_name = name or reporter.phone_e164 or ""

    if category.scope == ClaimScope.BUILDING:
        existing = session.scalars(
            select(Claim)
            .where(
                Claim.building_id == building_id,
                Claim.category_id == category_id,
                Claim.status.in_(OPEN_CLAIM_STATUSES),
            )
            .order_by(Claim.id)
            .limit(1)
        ).first()
        if existing is not None:
            if _already_in(existing, reporter):
                return CreateResult(existing, repeated=True, already_reporter=True)
            existing.reporters.append(
                ClaimReporter(
                    person_id=reporter.person_id,
                    name=shown_name,
                    phone_e164=reporter.phone_e164,
                    unit_id=reporter_unit.id if reporter_unit else None,
                    created_at=now,
                )
            )
            session.flush()
            _event(
                session,
                existing,
                ClaimEventKind.JOINED,
                actor,
                user,
                f"{shown_name}: {description}",
                now,
            )
            _audit(session, actor, user, "claim_joined", existing)
            session.flush()
            return CreateResult(existing, repeated=True)

    provider = session.get(Provider, assignment.provider_id) if assignment.provider_id else None
    if provider is not None and not provider.active:
        provider = None  # a deactivated provider is never notified: the studio attends it
    previous = _previous_claim(session, building_id, category, unit, reporter, now)
    if previous_claim_id is not None:
        previous = _own_closed_claim(session, previous_claim_id, reporter) or previous
    claim = Claim(
        building_id=building_id,
        unit_id=unit.id if category.scope == ClaimScope.UNIT and unit else None,
        category_id=category.id,
        scope=category.scope,
        urgent=category.urgent,
        description=description,
        follow_up_answer=_clean(follow_up_answer) or None,
        reporter_person_id=reporter.person_id,
        reporter_name=name or None,
        reporter_phone_e164=reporter.phone_e164,
        reporter_unit_id=reporter_unit.id if reporter_unit else None,
        provider_id=provider.id if provider else None,
        status=ClaimStatus.PENDING_SEND if provider else ClaimStatus.STUDIO,
        status_at=now,
        source=source,
        created_by_user=user if source == ClaimSource.PANEL else None,
        wa_conversation_id=wa_conversation_id,
        previous_claim_id=previous.id if previous else None,
        created_at=now,
    )
    session.add(claim)
    session.flush()
    _event(session, claim, ClaimEventKind.CREATED, actor, user, None, now)
    _audit(session, actor, user, "claim_created", claim)
    session.flush()
    return CreateResult(claim)


# --- Changing -------------------------------------------------------------------------------


def _move(claim: Claim, to: ClaimStatus, now: datetime) -> None:
    if claim.status in CLOSED_CLAIM_STATUSES:
        raise InvalidTransition("El reclamo ya está cerrado: no se puede cambiar.")
    if to not in TRANSITIONS[ClaimStatus(claim.status)]:
        raise InvalidTransition("Ese cambio no se puede hacer en este reclamo.")
    claim.status = to
    claim.status_at = now


def mark_sent(
    session: Session,
    claim: Claim,
    *,
    actor: ClaimActor = ClaimActor.SYSTEM,
    user: str | None = None,
    text: str | None = None,
    wa_message_id: int | None = None,
    now: datetime | None = None,
) -> None:
    """The provider was notified: by WhatsApp (app.claims.notify, with its message) or by
    phone ("Marcar como avisado" in the panel)."""
    now = _now(now)
    _move(claim, ClaimStatus.SENT, now)
    claim.sent_at = now
    claim.attention = None
    claim.send_after = None
    claim.reminded_at = None
    _event(
        session, claim, ClaimEventKind.SENT, actor, user, text, now, wa_message_id,
        claim.provider_id,
    )  # fmt: skip
    action = "claim_sent_to_provider" if wa_message_id else "claim_marked_sent"
    _audit(session, actor, user, action, claim)
    session.flush()


def provider_declined(session: Session, claim: Claim, *, now: datetime | None = None) -> None:
    """The provider said it cannot attend it ("No puedo atenderlo"): the studio attends it,
    and the panel shows an alert. The neighbor is not told."""
    if not claim.is_open:
        raise InvalidTransition("El reclamo ya está cerrado: no se puede cambiar.")
    now = _now(now)
    name = claim.provider.name if claim.provider else "El proveedor"
    provider_id = claim.provider_id
    if claim.status != ClaimStatus.STUDIO:
        _move(claim, ClaimStatus.STUDIO, now)
    claim.provider_id = None
    claim.sent_at = None
    claim.acknowledged_at = None
    claim.attention = ClaimAttention.DECLINED
    claim.send_after = None
    claim.reminded_at = None
    _event(
        session, claim, ClaimEventKind.DECLINED, ClaimActor.PROVIDER, None,
        f"{name} dijo que no puede atenderlo", now, provider_id=provider_id,
    )  # fmt: skip
    session.flush()


def needs_attention(session: Session, claim: Claim, attention: ClaimAttention | None) -> None:
    claim.attention = attention
    session.flush()


def mark_acknowledged(
    session: Session,
    claim: Claim,
    *,
    actor: ClaimActor = ClaimActor.PROVIDER,
    now: datetime | None = None,
) -> None:
    """The provider confirmed it got the claim."""
    now = _now(now)
    _move(claim, ClaimStatus.ACKNOWLEDGED, now)
    claim.acknowledged_at = now
    if claim.attention == ClaimAttention.NO_ACK:
        claim.attention = None
    _event(
        session, claim, ClaimEventKind.ACKNOWLEDGED, actor, None, None, now,
        provider_id=claim.provider_id,
    )  # fmt: skip
    session.flush()


def close_claim(
    session: Session,
    claim: Claim,
    status: ClaimStatus,
    reason: str,
    *,
    actor: ClaimActor,
    user: str | None = None,
    now: datetime | None = None,
) -> None:
    """Close it as solved or cancelled, with a reason. A closed claim never reopens."""
    if status not in CLOSED_CLAIM_STATUSES:
        raise InvalidTransition("Ese cambio no se puede hacer en este reclamo.")
    reason = _clean(reason)
    if not reason:
        raise ClaimProblem("Contá el motivo.")
    now = _now(now)
    _move(claim, status, now)
    claim.closed_at = now
    claim.attention = None
    claim.send_after = None
    claim.close_reason = reason
    kind = ClaimEventKind.SOLVED if status == ClaimStatus.SOLVED else ClaimEventKind.CANCELLED
    _event(session, claim, kind, actor, user, reason, now, provider_id=claim.provider_id)
    _audit(session, actor, user, f"claim_{status.value}", claim)
    session.flush()


def change_provider(
    session: Session,
    claim: Claim,
    provider_id: int | None,
    *,
    actor: ClaimActor,
    user: str | None = None,
    now: datetime | None = None,
) -> None:
    """Who attends an open claim: a provider (back to pending_send: it still has to be
    notified) or the studio (None). Nothing is sent."""
    if not claim.is_open:
        raise InvalidTransition("El reclamo ya está cerrado: no se puede cambiar.")
    provider = None
    if provider_id is not None:
        provider = session.get(Provider, provider_id)
        if provider is None or not provider.active:
            raise ClaimProblem("Elegí un proveedor activo.")
    if provider_id == claim.provider_id:
        raise ClaimProblem("No cambió quién lo atiende.")
    now = _now(now)
    to = ClaimStatus.PENDING_SEND if provider else ClaimStatus.STUDIO
    if to != claim.status:
        _move(claim, to, now)
    else:
        claim.status_at = now
    claim.provider_id = provider.id if provider else None
    claim.attention = None
    claim.send_after = None
    claim.reminded_at = None
    claim.sent_at = None
    claim.acknowledged_at = None
    _event(
        session,
        claim,
        ClaimEventKind.PROVIDER_CHANGED,
        actor,
        user,
        provider.name if provider else None,
        now,
    )
    _audit(session, actor, user, "claim_provider_changed", claim, fields=["provider_id"])
    session.flush()


def add_note(
    session: Session,
    claim: Claim,
    text: str,
    *,
    actor: ClaimActor,
    user: str | None = None,
    now: datetime | None = None,
) -> None:
    """An internal note (only the studio sees it); also on closed claims."""
    text = _clean(text)
    if not text:
        raise ClaimProblem("Escribí la nota.")
    _event(session, claim, ClaimEventKind.NOTE, actor, user, text, now)
    _audit(session, actor, user, "claim_note_added", claim)
    session.flush()


# --- Reading --------------------------------------------------------------------------------


def claims_of_person(
    session: Session,
    person_id: int | None,
    phone: str | None,
    now: datetime | None = None,
    limit: int = 10,
) -> list[Claim]:
    """A neighbor's claims (reported first or joined): the open ones and those closed in the
    last 30 days, newest first."""
    conditions = _reporter_condition(Reporter(name=None, phone_e164=phone, person_id=person_id))
    if not conditions:
        return []
    since = _now(now) - PREVIOUS_WINDOW
    return list(
        session.scalars(
            select(Claim)
            .where(
                or_(*conditions),
                or_(Claim.status.in_(OPEN_CLAIM_STATUSES), Claim.closed_at >= since),
            )
            .order_by(Claim.created_at.desc(), Claim.id.desc())
            .limit(limit)
        )
    )


def claim_of_person(
    session: Session, number: int, person_id: int | None, phone: str | None
) -> Claim | None:
    """The claim with that number, only if this neighbor reported it or joined it (None
    otherwise, the same as if it did not exist)."""
    conditions = _reporter_condition(Reporter(name=None, phone_e164=phone, person_id=person_id))
    if not conditions:
        return None
    return session.scalars(
        select(Claim).where(Claim.number == number, or_(*conditions)).limit(1)
    ).first()


def claims_for_phone(session: Session, e164: str, limit: int = 5) -> list[Claim]:
    """The latest claims of a phone: reported first or joined."""
    return list(
        session.scalars(
            select(Claim)
            .where(or_(*_reporter_condition(Reporter(name=None, phone_e164=e164))))
            .order_by(Claim.created_at.desc(), Claim.id.desc())
            .limit(limit)
        )
    )
