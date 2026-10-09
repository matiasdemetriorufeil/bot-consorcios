"""Metrics from bot_events (Córdoba days, current month). Invented events."""

from datetime import UTC, date, datetime, timedelta
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
    _event(session, oct_5, 3, "whatsapp_fixed_reply")
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


# --- Claims (8.6) ---------------------------------------------------------------------------


def _claims_month(session: Session) -> tuple[Any, Any]:
    from app.claims.service import (
        Reporter,
        change_provider,
        close_claim,
        create_claim,
        mark_acknowledged,
        mark_reminded,
        mark_sent,
        provider_declined,
    )
    from app.db.models import ClaimActor, ClaimSource, ClaimStatus
    from tests.bot import factories as f
    from tests.claims import factories as cf

    tower, house = (
        f.building(session, "001 TORRE INVENTADA"),
        f.building(session, "002 CASA FICTICIA"),
    )
    lift, leak = (
        cf.category(session, "Ascensor inventado"),
        cf.category(session, "Pérdida inventada"),
    )
    lifts = cf.provider(session, "Ascensores Ficticios SRL", "+5493515550111")
    plumber = cf.provider(session, "Plomería Inventada", "+5493515550112")
    for b in (tower, house):
        cf.assign(session, b.id, lift, lifts)
        cf.assign(session, b.id, leak, plumber)
    t0 = datetime(2026, 10, 2, 13, 0, tzinfo=UTC)
    hour = timedelta(hours=1)

    def new(building: Any, category: Any, at: datetime) -> Any:
        return create_claim(
            session, building_id=building.id, category_id=category.id, unit_id=None,
            description="Inventado", reporter=Reporter(name="Ana Inventada"),
            source=ClaimSource.PANEL, now=at,
        ).claim  # fmt: skip

    # 1: the lifts confirm after 2 h and solve after 10 h.
    a = new(tower, lift, t0)
    mark_sent(session, a, now=t0)
    mark_acknowledged(session, a, now=t0 + 2 * hour)
    close_claim(
        session, a, ClaimStatus.SOLVED, "Listo", actor=ClaimActor.PROVIDER, now=t0 + 10 * hour
    )
    # 2: the lifts get a reminder and confirm after 4 h.
    b = new(house, lift, t0)
    mark_sent(session, b, now=t0)
    mark_reminded(session, b, "Recordatorio", wa_message_id=None, now=t0 + 3 * hour)
    mark_acknowledged(session, b, now=t0 + 4 * hour)
    # 3: the plumber cannot; it goes to the lifts.
    c = new(tower, leak, t0)
    mark_sent(session, c, now=t0)
    provider_declined(session, c, now=t0 + hour)
    change_provider(session, c, lifts.id, actor=ClaimActor.PANEL, user="admin", now=t0 + 2 * hour)
    # Last month: does not count.
    old = new(house, leak, datetime(2026, 9, 20, 13, 0, tzinfo=UTC))
    mark_sent(session, old, now=datetime(2026, 9, 20, 13, 0, tzinfo=UTC))
    session.flush()
    return lifts, plumber


def test_claim_metrics(db_session: Session) -> None:
    from app.admin.metrics import compute_claim_metrics

    _claims_month(db_session)
    c = compute_claim_metrics(db_session, NOW, TZ)
    assert c.month_start == date(2026, 10, 1) and c.total == 3
    # In the order of the statuses, only the ones with claims.
    assert c.by_status == [
        ("Falta avisar al proveedor", 1), ("El proveedor lo confirmó", 1), ("Solucionado", 1)
    ]  # fmt: skip
    assert c.by_building == [("TORRE INVENTADA", 2), ("CASA FICTICIA", 1)]
    assert dict(c.by_category) == {"Ascensor inventado": 2, "Pérdida inventada": 1}
    [lifts, plumber] = c.providers
    assert (lifts.name, lifts.sent, lifts.declined, lifts.reminded) == (
        "Ascensores Ficticios SRL",
        2,
        0,
        1,
    )
    assert lifts.hours_to_ack == 3.0  # (2 + 4) / 2
    assert lifts.hours_to_solve == 10.0
    assert (plumber.name, plumber.sent, plumber.declined, plumber.reminded) == (
        "Plomería Inventada",
        1,
        1,
        0,
    )
    assert plumber.hours_to_ack is None and plumber.hours_to_solve is None


def test_claim_metrics_on_the_page_without_personal_data(logged_in: Panel) -> None:
    _claims_month(logged_in.session)
    logged_in.session.commit()
    page = logged_in.client.get("/admin/metrics").text
    assert "Reclamos del mes" in page and 'id="claims-by-provider"' in page
    assert "Ascensores Ficticios SRL" in page and "TORRE INVENTADA" in page
    assert "Ana Inventada" not in page and "5550111" not in page
