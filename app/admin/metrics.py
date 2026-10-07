"""Simple bot metrics for the admin panel, computed from bot_events (Córdoba time).

- Conversations per day: distinct conversations with any event that day.
- Handed off: share of this month's conversations with a "handoff" event.
- Most used tools: "tool_call" events of the month.
- AI cost of the month: sum of the estimated cost of each model call ("llm_usage"; not
  "agent_turn", which repeats the same cost per turn).
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import Numeric, cast, func, select
from sqlalchemy.orm import Session

from app.db.models import BotEvent

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
