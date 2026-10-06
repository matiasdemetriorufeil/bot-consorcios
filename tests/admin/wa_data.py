"""Invented WhatsApp conversations for the inbox tests."""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import (
    WaAuthor,
    WaContact,
    WaConversation,
    WaConversationStatus,
    WaDirection,
    WaMessage,
    WaMessageStatus,
)
from tests.admin.conftest import NOW

_counter = iter(range(1, 10_000))


def conversation(
    session: Session,
    wa_id: str = "5493515550101",
    *,
    profile_name: str | None = "Contacto Inventado",
    status: WaConversationStatus = WaConversationStatus.BOT,
    last_inbound_at: datetime | None = NOW - timedelta(hours=1),
    **values: Any,
) -> WaConversation:
    contact = WaContact(phone_e164=f"+{wa_id}", wa_id=wa_id, profile_name=profile_name)
    session.add(contact)
    session.flush()
    row = WaConversation(
        contact_id=contact.id, status=status, last_inbound_at=last_inbound_at, **values
    )
    session.add(row)
    session.flush()
    return row


def message(
    session: Session,
    conv: WaConversation,
    body: str | None = "hola",
    *,
    author: WaAuthor = WaAuthor.CONTACT,
    at: datetime | None = None,
    **values: Any,
) -> WaMessage:
    inbound = author == WaAuthor.CONTACT
    row = WaMessage(
        conversation_id=conv.id,
        direction=WaDirection.INBOUND if inbound else WaDirection.OUTBOUND,
        author=author,
        message_type=values.pop("message_type", "text"),
        body=body,
        wa_message_id=values.pop("wa_message_id", f"wamid.inventado.{next(_counter)}"),
        status=values.pop("status", WaMessageStatus.RECEIVED if inbound else WaMessageStatus.SENT),
        created_at=at or NOW - timedelta(minutes=30),
        **values,
    )
    session.add(row)
    session.flush()
    return row
