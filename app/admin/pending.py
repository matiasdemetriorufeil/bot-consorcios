"""What waits for someone of the studio, in numbers: the page «Pendientes» (its cards), the
menu's counters, the tab's title and the alerts of every page of the panel. No HTTP here
(app.admin.pending_view serves it), so it is tested on its own.

One light request (GET /admin/pending/numbers) every few seconds from every page brings all of
it. Its JSON carries numbers, short texts already formatted, ids and the claims' alerts the
panel already showed (problem and building): no names, phones nor units. The admin's row is
only computed (and only sent) for admins.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admin import formatting, labels
from app.admin.claims import NEEDS_SOMEONE, claims_stamp, urgent_open_claims
from app.db.models import (
    OPEN_CLAIM_STATUSES,
    Amenity,
    Building,
    BuildingClaimCategory,
    Claim,
    Phone,
    Provider,
    Reservation,
    ReservationStatus,
    SyncKind,
    SyncRun,
    SyncStatus,
    VerificationRequest,
    VerificationRequestStatus,
    WaConversation,
    WaConversationStatus,
    WaDirection,
    WaMessage,
)

# How far back «la última sincronización» looks (the nightly runs of last night).
SYNC_WINDOW = timedelta(hours=36)
# Worst first: the run that tells how last night went.
_SYNC_ORDER = {SyncStatus.FAILED: 0, SyncStatus.PARTIAL: 1, None: 2, SyncStatus.OK: 3}
NO_SYNC = "No hubo sincronización anoche"
SYNC_RUNNING = "En curso"

WAITING = WaConversation.status == WaConversationStatus.WAITING_HUMAN
WAITING_URGENT = WAITING & (WaConversation.handoff_priority == "urgent")
URGENT_OPEN_CLAIM = Claim.status.in_(OPEN_CLAIM_STATUSES) & Claim.urgent.is_(True)


@dataclass(frozen=True)
class SumDay:
    label: str  # "Hoy" | "Mañana"
    day: date
    # (building, amenity id, reservations), by building.
    buildings: list[tuple[str, int, int]]

    @property
    def total(self) -> int:
        return sum(n for _, _, n in self.buildings)


@dataclass(frozen=True)
class AdminRow:
    sync_text: str
    sync_problem: bool  # failed, with errors, or none last night
    providers_without_whatsapp: int
    no_answer_week: int


@dataclass(frozen=True)
class Pending:
    waiting: int
    waiting_urgent: int
    waiting_oldest: str  # "hace 25 min" ("" if none)
    mine: int
    claims: int
    claims_urgent: int
    phones: int
    verifications: int
    verifications_oldest: str  # "lunes 05/10" ("" if none)
    sum_days: list[SumDay]
    # For the alerts: a key per thing that waits (a new key = something new).
    waiting_keys: list[str] = field(default_factory=list)
    mine_keys: list[dict[str, Any]] = field(default_factory=list)  # {"id": conv., "key": ...}
    claim_alerts: list[dict[str, Any]] = field(default_factory=list)
    # Changes whenever a claim changes (the list of «Reclamos» redraws itself then).
    claims_stamp: str = ""
    admin: AdminRow | None = None

    @property
    def all_clear(self) -> bool:
        """Nothing waits (the SUM's reservations are only information)."""
        return not (self.waiting or self.mine or self.claims or self.phones or self.verifications)

    @property
    def title_count(self) -> int:
        """The «(N)» of the browser's tab: people waiting and urgent claims."""
        return self.waiting + self.claims_urgent


def _count(session: Session, model: Any, condition: Any) -> int:
    return session.scalar(select(func.count()).select_from(model).where(condition)) or 0


def counts(session: Session) -> dict[str, int]:
    """The menu's counters (no user needed): the same numbers as the cards."""
    return {
        "waiting": _count(session, WaConversation, WAITING),
        "waiting_urgent": _count(session, WaConversation, WAITING_URGENT),
        "claims": _count(session, Claim, NEEDS_SOMEONE),
        "claims_urgent": _count(session, Claim, URGENT_OPEN_CLAIM),
        # «A revisar» of Teléfonos (app.admin.phones.REVIEW).
        "phones": _count(session, Phone, Phone.needs_review.is_(True) | Phone.conflict.is_(True)),
        "verifications": _count(
            session,
            VerificationRequest,
            VerificationRequest.status == VerificationRequestStatus.PENDING,
        ),
    }


def mine_unanswered(session: Session, user: str) -> list[tuple[int, int]]:
    """My conversations (with a person, assigned to me) whose last message, not counting the
    internal notes, is the contact's: (conversation id, that message's id)."""
    last = (
        select(WaMessage.conversation_id, func.max(WaMessage.id).label("last_id"))
        .join(WaConversation, WaConversation.id == WaMessage.conversation_id)
        .where(
            WaConversation.status == WaConversationStatus.HUMAN,
            WaConversation.assigned_to == user,
            WaMessage.is_internal_note.is_(False),
        )
        .group_by(WaMessage.conversation_id)
        .subquery()
    )
    rows = session.execute(
        select(last.c.conversation_id, last.c.last_id)
        .join(WaMessage, WaMessage.id == last.c.last_id)
        .where(WaMessage.direction == WaDirection.INBOUND)
        .order_by(last.c.conversation_id)
    ).all()
    return [(conversation_id, last_id) for conversation_id, last_id in rows]


def _waiting_keys(session: Session) -> list[str]:
    """conv:<id>:<handoff>: a conversation handed off again alerts again."""
    rows = session.execute(
        select(WaConversation.id, WaConversation.handed_off_at)
        .where(WAITING)
        .order_by(WaConversation.id)
        .limit(500)
    ).all()
    return [f"conv:{i}:{int(at.timestamp()) if at else 0}" for i, at in rows]


def sum_days(session: Session, today: date) -> list[SumDay]:
    """The confirmed reservations of today and tomorrow, by building."""
    rows = session.execute(
        select(Reservation.date, Building.name, Amenity.id, func.count(Reservation.id))
        .join(Amenity, Amenity.id == Reservation.amenity_id)
        .join(Building, Building.id == Amenity.building_id)
        .where(
            Reservation.status == ReservationStatus.CONFIRMED,
            Reservation.date.between(today, today + timedelta(days=1)),
        )
        .group_by(Reservation.date, Building.name, Amenity.id)
    ).all()
    days = []
    for offset, label in ((0, "Hoy"), (1, "Mañana")):
        day = today + timedelta(days=offset)
        buildings = sorted(
            (formatting.building(name), amenity_id, n)
            for when, name, amenity_id, n in rows
            if when == day
        )
        days.append(SumDay(label, day, buildings))
    return days


def last_sync(session: Session, now: datetime, timezone: str) -> tuple[str, bool]:
    """How last night's reading of ConsorPlus went: the worst of the nightly runs of the last
    36 hours, with its time. (text, whether something is wrong)."""
    runs = session.scalars(
        select(SyncRun).where(
            SyncRun.kind == SyncKind.NIGHTLY, SyncRun.started_at >= now - SYNC_WINDOW
        )
    ).all()
    if not runs:
        return NO_SYNC, True
    worst = min(runs, key=lambda r: (_SYNC_ORDER.get(r.status, 2), -r.started_at.timestamp()))
    status = SYNC_RUNNING if worst.status is None else labels.SYNC_STATUS[worst.status]
    when = formatting.when(worst.started_at, now=now, timezone=timezone)
    problem = worst.status in (SyncStatus.FAILED, SyncStatus.PARTIAL)
    return f"{status} ({when})", problem


def admin_row(session: Session, now: datetime, timezone: str) -> AdminRow:
    sync_text, sync_problem = last_sync(session, now, timezone)
    providers = (
        session.scalar(
            select(func.count(func.distinct(Provider.id)))
            .join(BuildingClaimCategory, BuildingClaimCategory.provider_id == Provider.id)
            .where(
                Provider.active.is_(True),
                Provider.whatsapp_e164.is_(None),
                BuildingClaimCategory.enabled.is_(True),
            )
        )
        or 0
    )
    no_answer = _count(
        session,
        WaConversation,
        (WaConversation.handoff_reason == "no_answer")
        & (WaConversation.handed_off_at >= formatting.week_start(now, timezone)),
    )
    return AdminRow(sync_text, sync_problem, providers, no_answer)


def pending_numbers(
    session: Session, user: str, now: datetime, timezone: str, *, admin: bool
) -> Pending:
    """Everything «Pendientes» shows, for this user (and the admin's row only for admins)."""
    numbers = counts(session)
    oldest_waiting = session.scalar(select(func.min(WaConversation.handed_off_at)).where(WAITING))
    oldest_verification = session.scalar(
        select(func.min(VerificationRequest.created_at)).where(
            VerificationRequest.status == VerificationRequestStatus.PENDING
        )
    )
    mine = mine_unanswered(session, user)
    today = now.astimezone(ZoneInfo(timezone)).date()
    return Pending(
        waiting=numbers["waiting"],
        waiting_urgent=numbers["waiting_urgent"],
        waiting_oldest=formatting.ago(oldest_waiting, now) if oldest_waiting else "",
        mine=len(mine),
        claims=numbers["claims"],
        claims_urgent=numbers["claims_urgent"],
        phones=numbers["phones"],
        verifications=numbers["verifications"],
        verifications_oldest=(
            formatting.weekday_day(oldest_verification, timezone) if oldest_verification else ""
        ),
        sum_days=sum_days(session, today),
        waiting_keys=_waiting_keys(session),
        mine_keys=[{"id": c, "key": f"mine:{c}:{m}"} for c, m in mine],
        claim_alerts=urgent_open_claims(session, now),
        claims_stamp=claims_stamp(session),
        admin=admin_row(session, now, timezone) if admin else None,
    )


def to_json(pending: Pending) -> dict[str, Any]:
    """The poll's answer (without the cards)."""
    data: dict[str, Any] = {
        "counts": {
            "waiting": pending.waiting,
            "waiting_urgent": pending.waiting_urgent,
            "mine": pending.mine,
            "claims": pending.claims,
            "claims_urgent": pending.claims_urgent,
            "phones": pending.phones,
            "verifications": pending.verifications,
        },
        "title_count": pending.title_count,
        "claims_stamp": pending.claims_stamp,
        "alerts": {
            "waiting": pending.waiting_keys,
            "mine": pending.mine_keys,
            "claims": pending.claim_alerts,
        },
    }
    if pending.admin is not None:
        data["admin"] = {
            "sync": pending.admin.sync_text,
            "sync_problem": pending.admin.sync_problem,
            "providers_without_whatsapp": pending.admin.providers_without_whatsapp,
            "no_answer_week": pending.admin.no_answer_week,
        }
    return data
