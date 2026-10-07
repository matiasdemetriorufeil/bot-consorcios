"""The example conversation of "Cómo usar": a made-up inbox (three conversations, one urgent
and open, with the contact's messages, the bot's with buttons, the handoff note and the contact
card), drawn with the same templates as the real one (conversations.html).

Fixed data, built here on every request: nothing is read from or written to the database and
nothing can be sent (the templates render its buttons as forms that do nothing, see
EXAMPLE_NOTES in app.admin.help). Clearly invented: "Ana Ejemplo", 351 555-0101, "TORRE EJEMPLO".
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from app.admin import formatting, help, inbox, labels
from app.db.models import WaConversationStatus

WAITING = WaConversationStatus.WAITING_HUMAN
BUILDING = "TORRE EJEMPLO"
ANA = "+5493515550101"
BRUNO = "+5493515550102"
CARLA = "+5493515550103"


def _message(
    n: int, side: str, author: str, body: str, at: datetime, timezone: str, **extra: Any
) -> inbox.MessageView:
    local = at.astimezone(ZoneInfo(timezone))
    values: dict[str, Any] = {
        "id": n,
        "side": side,
        "author": author,
        "author_name": {"bot": "Bot", "system": "Sistema"}.get(author, ""),
        "kind_label": "",
        "body": body,
        "choices": [],
        "is_handoff_note": False,
        "media_url_ok": False,
        "media_inline": False,
        "media_problem": "",
        "media_filename": "",
        "status": "read" if side == "out" else "",
        "error_text": "",
        "time": f"{local:%H:%M}",
        "day": f"{local:%d/%m/%Y}",
    }
    values.update(extra)
    return inbox.MessageView(**values)


def _row(
    n: int, name: str, phone: str, status: WaConversationStatus, minutes: int, now: datetime,
    preview: str, *, units: list[inbox.UnitLine], urgent: bool = False,
    reason: str | None = None, unread: int = 0,
) -> inbox.InboxRow:  # fmt: skip
    when = now - timedelta(minutes=minutes)
    return inbox.InboxRow(
        id=n,
        name=name,
        phone=phone,
        status=status,
        status_label=labels.CONVERSATION_STATUS[status],
        units=units,
        unread=unread,
        last_at=when,
        ago=inbox.ago(when, now),
        preview=preview,
        urgent=urgent,
        reason=labels.reason(reason),
        assigned_to=None,
        from_phone_app=False,
    )


def context(now: datetime, timezone: str, tab: str) -> dict[str, Any]:
    """Everything conversations.html reads, invented (tab: the real one to go back to)."""
    ana_unit = inbox.UnitLine(unit_id=1, building=BUILDING, label="4-B", role="propietario")
    rows = [
        _row(1, "Ana Ejemplo", ANA, WAITING, 4, now, "¿Pueden venir a ver?",
             units=[ana_unit], urgent=True, reason="emergency", unread=2),
        _row(2, "Bruno Prueba", BRUNO, WAITING, 35, now, "Me figura una deuda que ya pagué",
             units=[inbox.UnitLine(2, BUILDING, "2-A", "propietario")], reason="debt_claim",
             unread=1),
        _row(3, "Carla Inventada", CARLA, WaConversationStatus.BOT, 90, now,
             "Gracias, con eso me alcanza", units=[]),
    ]  # fmt: skip
    m = [
        _message(1, "in", "contact", "Hola, en el palier del 4° hay olor a gas", now - timedelta(
            minutes=9), timezone),
        _message(2, "out", "bot", "Uh, gracias por avisar. ¿Querés que te pase con alguien del "
                 "estudio?", now - timedelta(minutes=8), timezone,
                 choices=["Hablar con una persona", "Ver mi deuda"]),
        _message(3, "in", "contact", "Hablar con una persona", now - timedelta(minutes=7),
                 timezone),
        _message(4, "note", "system", "Derivada: Urgencia en el edificio. Ana Ejemplo — "
                 "TORRE EJEMPLO 4-B (propietaria).", now - timedelta(minutes=7), timezone,
                 is_handoff_note=True, status=""),
        _message(5, "in", "contact", "¿Pueden venir a ver?", now - timedelta(minutes=4),
                 timezone),
    ]  # fmt: skip
    today = now.astimezone(ZoneInfo(timezone)).date()
    card = inbox.ContactCard(
        name="Ana Ejemplo",
        profile_name="Ana",
        phone=ANA,
        known=True,
        verified=True,
        units=[
            inbox.CardUnit(
                unit=ana_unit,
                debt=inbox.DebtLine(
                    total=formatting.money(12345),
                    up_to_date=False,
                    fetched_at="hoy 09:00",
                ),
            )
        ],
        reservations=[
            inbox.CardReservation(
                building=BUILDING,
                unit="4-B",
                day=today + timedelta(days=(4 - today.weekday()) % 7 or 7),
                slot="20:00 a 02:00 (del día siguiente) · Noche",
            )
        ],
    )
    conversation = SimpleNamespace(
        id=1,
        status=WAITING,
        assigned_to=None,
        handoff_priority="urgent",
        handoff_summary="Olor a gas en el palier del 4° piso. Pide que alguien vaya a ver.",
    )
    return {
        "title": "Conversaciones",
        "example": True,
        "example_notes": help.EXAMPLE_NOTES,
        "example_banner": help.EXAMPLE_BANNER,
        "example_done": help.EXAMPLE_DONE,
        "back_tab": tab,
        # The list
        "rows": rows,
        "tab": inbox.DEFAULT_TAB,
        "tabs": inbox.TABS,
        "query": "",
        "counts": {"waiting": 2, "mine": 0},
        "names": {},
        # The open conversation
        "open_id": 1,
        "conversation": conversation,
        "contact": SimpleNamespace(phone_e164=ANA),
        "status_label": labels.CONVERSATION_STATUS[WAITING],
        "assigned_name": None,
        "reason": labels.reason("emergency"),
        "card": card,
        "messages": m,
        "last_id": m[-1].id,
        "window_open": True,
        "window_closed_text": inbox.WINDOW_CLOSED_TEXT,
        "templates_list": [
            SimpleNamespace(id=1, label="Retomar la charla", body="Hola, te escribimos.")
        ],
        "quick_replies": [
            SimpleNamespace(title="Saludo", content="¡Hola! Te escribo del estudio."),
            SimpleNamespace(title="Ya vamos", content="Ya avisamos al encargado, va en camino."),
        ],
        "whatsapp_ready": True,
        "form_token": "",
        "tour_auto": True,
        "tour_steps": {"steps": help.TOUR, "missing": help.TOUR_MISSING},
    }
