"""Metrics from bot_events (Córdoba days, current month). Invented events."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.admin.metrics import DAYS, compute_metrics
from app.db.models import BotEvent
from tests.admin.conftest import Panel

TZ = "America/Argentina/Cordoba"
NOW = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)  # Monday 12:00 in Córdoba


def _event(session: Session, when: datetime, conversation: int | None, kind: str, **payload: Any):
    session.add(
        BotEvent(conversation_id=conversation, event_type=kind, payload=payload, created_at=when)
    )


def _seed(session: Session) -> None:
    oct_4_night = datetime(2026, 10, 5, 2, 0, tzinfo=UTC)  # Oct 4 23:00 in Córdoba
    oct_5 = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)
    september = datetime(2026, 9, 20, 13, 0, tzinfo=UTC)
    _event(session, oct_4_night, 1, "agent_turn", cost_usd=0.002)
    _event(session, oct_4_night, 1, "tool_call", tool="find_unit", status="found")
    _event(session, oct_4_night, 1, "llm_usage", cost_usd=0.0015)
    _event(session, oct_4_night, 1, "llm_usage", cost_usd=0.0005)
    _event(session, oct_5, 2, "tool_call", tool="get_debt", status="ok")
    _event(session, oct_5, 2, "tool_call", tool="find_unit", status="found")
    _event(session, oct_5, 2, "handoff", reason="payment_plan")
    _event(session, oct_5, 2, "llm_usage", cost_usd=None)
    _event(session, oct_5, 3, "chatwoot_fixed_reply")
    _event(session, oct_5, None, "admin_action", action="phone_approved")
    _event(session, september, 9, "tool_call", tool="get_debt")
    _event(session, september, 9, "llm_usage", cost_usd=5)
    _event(session, september, 9, "handoff", reason="upset")
    session.flush()


def test_compute_metrics(db_session: Session) -> None:
    _seed(db_session)
    m = compute_metrics(db_session, NOW, TZ)

    per_day = dict(m.conversations_per_day)
    assert len(m.conversations_per_day) == DAYS
    assert m.conversations_per_day[-1][0] == date(2026, 10, 5)
    assert per_day[date(2026, 10, 4)] == 1  # 23:00 Córdoba, already Oct 5 in UTC
    assert per_day[date(2026, 10, 5)] == 2
    assert per_day[date(2026, 9, 20)] == 1
    assert per_day[date(2026, 9, 21)] == 0

    assert m.month_start == date(2026, 10, 1)
    assert m.month_conversations == 3
    assert m.month_handed_off == 1
    assert round(m.handed_off_percent or 0, 1) == 33.3
    assert m.top_tools == [("find_unit", 2), ("get_debt", 1)]
    assert m.month_cost_usd == Decimal("0.002")  # llm_usage only, not agent_turn
    assert m.month_calls_without_cost == 1


def test_no_data(db_session: Session) -> None:
    m = compute_metrics(db_session, datetime(2030, 1, 15, tzinfo=UTC), TZ)
    assert m.month_conversations == 0 and m.handed_off_percent is None
    assert m.top_tools == [] and m.month_cost_usd == 0


def test_metrics_page(logged_in: Panel) -> None:
    _seed(logged_in.session)
    logged_in.session.commit()
    page = logged_in.client.get("/admin/metrics")
    assert page.status_code == 200
    assert "Conversaciones por día" in page.text and "Herramientas más usadas" in page.text
