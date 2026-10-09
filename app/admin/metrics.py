"""Simple bot metrics for the admin panel, computed from bot_events (Córdoba time).

- Conversations per day: distinct conversations with any event that day.
- Handed off: share of this month's conversations with a "handoff" event.
- Most used tools: "tool_call" events of the month.
- AI cost of the month: sum of the estimated cost of each model call ("llm_usage"; not
  "agent_turn", which repeats the same cost per turn).
- Claims of the month (compute_claim_metrics): by status, building and problem, and per
  provider (from the history, so a claim moved to another provider counts for each one): how
  many were sent to it, the average real hours from the send to its "Recibido" and to solved,
  how many it could not attend and how many needed a reminder. No personal data.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import Numeric, cast, func, select
from sqlalchemy.orm import Session

from app.admin import formatting, labels
from app.db.models import (
    BotEvent,
    Building,
    Claim,
    ClaimCategory,
    ClaimEvent,
    ClaimEventKind,
    ClaimStatus,
    Provider,
)

DAYS = 30
TOP_TOOLS = 10


@dataclass(frozen=True)
class Metrics:
    month_start: date
    conversations_per_day: list[tuple[date, int]]  # oldest first, days without any: 0
    month_conversations: int
    month_handed_off: int
    top_tools: list[tuple[str, int]]
    month_cost_usd: Decimal
    month_calls_without_cost: int  # model calls with no price configured

    @property
    def handed_off_percent(self) -> float | None:
        if not self.month_conversations:
            return None
        return 100 * self.month_handed_off / self.month_conversations


def _distinct_conversations(session: Session, since: datetime, event_type: str | None) -> int:
    stmt = select(func.count(func.distinct(BotEvent.conversation_id))).where(
        BotEvent.conversation_id.is_not(None), BotEvent.created_at >= since
    )
    if event_type:
        stmt = stmt.where(BotEvent.event_type == event_type)
    return session.scalar(stmt) or 0


def compute_metrics(session: Session, now: datetime, timezone: str) -> Metrics:
    tz = ZoneInfo(timezone)
    local_now = now.astimezone(tz)
    today = local_now.date()
    first_day = today - timedelta(days=DAYS - 1)
    since = datetime.combine(first_day, time(), tz)
    month_start = datetime.combine(today.replace(day=1), time(), tz)

    local_day = func.date(func.timezone(timezone, BotEvent.created_at))
    rows = session.execute(
        select(local_day, func.count(func.distinct(BotEvent.conversation_id)))
        .where(BotEvent.conversation_id.is_not(None), BotEvent.created_at >= since)
        .group_by(local_day)
    ).all()
    per_day = dict(rows)
    conversations_per_day = [
        (day, per_day.get(day, 0)) for day in (first_day + timedelta(d) for d in range(DAYS))
    ]

    tool = BotEvent.payload["tool"].astext
    top_tools = session.execute(
        select(tool, func.count())
        .where(BotEvent.event_type == "tool_call", BotEvent.created_at >= month_start)
        .group_by(tool)
        .order_by(func.count().desc(), tool)
        .limit(TOP_TOOLS)
    ).all()

    cost = cast(BotEvent.payload["cost_usd"].astext, Numeric(14, 6))
    month_usage = (BotEvent.event_type == "llm_usage", BotEvent.created_at >= month_start)
    total_cost = session.scalar(select(func.coalesce(func.sum(cost), 0)).where(*month_usage))
    without_cost = session.scalar(
        select(func.count()).where(*month_usage, BotEvent.payload["cost_usd"].astext.is_(None))
    )

    return Metrics(
        month_start=month_start.date(),
        conversations_per_day=conversations_per_day,
        month_conversations=_distinct_conversations(session, month_start, None),
        month_handed_off=_distinct_conversations(session, month_start, "handoff"),
        top_tools=[(name or "?", n) for name, n in top_tools],
        month_cost_usd=Decimal(total_cost or 0),
        month_calls_without_cost=without_cost or 0,
    )


# --- Claims ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderMetrics:
    name: str
    sent: int
    hours_to_ack: float | None  # average, from the send to its "Recibido"
    hours_to_solve: float | None  # average, from the send to solved
    declined: int
    reminded: int


@dataclass(frozen=True)
class ClaimMetrics:
    month_start: date
    total: int
    by_status: list[tuple[str, int]]
    by_building: list[tuple[str, int]]
    by_category: list[tuple[str, int]]
    providers: list[ProviderMetrics]


def _average_hours(spans: list[timedelta]) -> float | None:
    if not spans:
        return None
    return sum(span.total_seconds() for span in spans) / len(spans) / 3600


def _spans(claims: list[dict[str, datetime]], kind: ClaimEventKind) -> list[timedelta]:
    """From the send to `kind`, for the claims that got there."""
    sent = ClaimEventKind.SENT
    return [c[kind] - c[sent] for c in claims if kind in c and c[kind] >= c[sent]]


_PROVIDER_KINDS = (
    ClaimEventKind.SENT, ClaimEventKind.ACKNOWLEDGED, ClaimEventKind.SOLVED,
    ClaimEventKind.DECLINED, ClaimEventKind.REMINDED,
)  # fmt: skip


def compute_claim_metrics(session: Session, now: datetime, timezone: str) -> ClaimMetrics:
    tz = ZoneInfo(timezone)
    month_start = datetime.combine(now.astimezone(tz).date().replace(day=1), time(), tz)
    in_month = Claim.created_at >= month_start

    statuses = {
        ClaimStatus(status): n
        for status, n in session.execute(
            select(Claim.status, func.count(Claim.id)).where(in_month).group_by(Claim.status)
        )
    }
    by_status = [(labels.CLAIM_STATUS[s], statuses[s]) for s in ClaimStatus if statuses.get(s)]
    buildings = session.execute(
        select(Building.name, func.count(Claim.id))
        .join(Claim, Claim.building_id == Building.id)
        .where(in_month)
        .group_by(Building.name)
        .order_by(func.count(Claim.id).desc(), Building.name)
    ).all()
    categories = session.execute(
        select(ClaimCategory.list_title, func.count(Claim.id))
        .join(Claim, Claim.category_id == ClaimCategory.id)
        .where(in_month)
        .group_by(ClaimCategory.list_title)
        .order_by(func.count(Claim.id).desc(), ClaimCategory.list_title)
    ).all()

    # Per provider: the first of each kind for each (provider, claim) of the month's history.
    events = session.execute(
        select(
            Provider.id, Provider.name, ClaimEvent.claim_id, ClaimEvent.kind, ClaimEvent.created_at
        )
        .join(Provider, Provider.id == ClaimEvent.provider_id)
        .where(ClaimEvent.created_at >= month_start, ClaimEvent.kind.in_(_PROVIDER_KINDS))
        .order_by(ClaimEvent.created_at, ClaimEvent.id)
    ).all()
    names: dict[int, str] = {}
    firsts: dict[int, dict[int, dict[str, datetime]]] = {}
    declined: dict[int, int] = {}
    for provider_id, name, claim_id, kind, created_at in events:
        names[provider_id] = name
        if kind == ClaimEventKind.DECLINED:
            declined[provider_id] = declined.get(provider_id, 0) + 1
        by_claim = firsts.setdefault(provider_id, {}).setdefault(claim_id, {})
        by_claim.setdefault(str(kind), created_at)
    providers = []
    for provider_id in sorted(firsts, key=lambda i: names[i].casefold()):
        claims = [c for c in firsts[provider_id].values() if ClaimEventKind.SENT in c]
        providers.append(
            ProviderMetrics(
                name=names[provider_id],
                sent=len(claims),
                hours_to_ack=_average_hours(_spans(claims, ClaimEventKind.ACKNOWLEDGED)),
                hours_to_solve=_average_hours(_spans(claims, ClaimEventKind.SOLVED)),
                declined=declined.get(provider_id, 0),
                reminded=sum(ClaimEventKind.REMINDED in c for c in claims),
            )
        )
    return ClaimMetrics(
        month_start=month_start.date(),
        total=sum(statuses.values()),
        by_status=by_status,
        by_building=[(formatting.building(n), c) for n, c in buildings],
        by_category=[(t, c) for t, c in categories],
        providers=providers,
    )
