"""What a webhook delivery leaves in the database (inside the request: quick, no HTTP).

Each WhatsApp message is stored once: wa_messages.wa_message_id is unique and the insert is
ON CONFLICT DO NOTHING, so a delivery Meta repeats is ignored (it retries until it gets a 200).
"""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import (
    WaAuthor,
    WaContact,
    WaConversation,
    WaDirection,
    WaMessage,
    WaMessageStatus,
)
from app.whatsapp import conversations
from app.whatsapp.events import WaIncoming, WaStatus

logger = logging.getLogger(__name__)

# A status only moves forward (Meta may deliver them out of order). "played" (v25+, a voice
# note listened to) counts as read.
_RANK = {
    WaMessageStatus.SENT: 1,
    WaMessageStatus.DELIVERED: 2,
    WaMessageStatus.READ: 3,
}
_STATUS_ALIASES = {"played": WaMessageStatus.READ}


def _rank(status: WaMessageStatus | None) -> int:
    return _RANK.get(status, 0) if status is not None else 0


def contact_conversation(
    session: Session, wa_id: str, profile_name: str | None = None
) -> WaConversation:
    """The contact's one conversation (both created the first time; safe with concurrent
    deliveries)."""
    digits = wa_id.lstrip("+")
    values = {"phone_e164": f"+{digits}", "wa_id": digits}
    insert = pg_insert(WaContact).values(**values, profile_name=profile_name)
    if profile_name:
        stmt = insert.on_conflict_do_update(
            index_elements=["phone_e164"], set_={"profile_name": profile_name}
        )
    else:
        stmt = insert.on_conflict_do_nothing(index_elements=["phone_e164"])
    session.execute(stmt)
    contact_id = session.scalar(select(WaContact.id).where(WaContact.phone_e164 == f"+{digits}"))
    session.execute(
        pg_insert(WaConversation)
        .values(contact_id=contact_id)
        .on_conflict_do_nothing(index_elements=["contact_id"])
    )
    conversation = session.scalar(
        select(WaConversation).where(WaConversation.contact_id == contact_id).with_for_update()
    )
    if conversation is None:  # just inserted (or already there)
        raise LookupError(contact_id)
    return conversation


def _insert_message(session: Session, **values: object) -> int | None:
    """The new message's id, or None if its wamid was already stored."""
    stmt = (
        pg_insert(WaMessage)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["wa_message_id"])
        .returning(WaMessage.id)
    )
    return session.execute(stmt).scalar()


def _media_values(message: WaIncoming) -> dict[str, object]:
    if message.media is None:
        return {}
    return {
        "media_id": message.media.media_id,
        "media_mime": message.media.mime_type,
        "media_filename": message.media.filename,
    }


def record_incoming(session: Session, message: WaIncoming, now: datetime) -> int | None:
    """Stores a message of the contact and updates its conversation (the caller commits).
    Returns the new message's id, or None for a duplicate."""
    conversation = contact_conversation(session, message.wa_id, message.profile_name)
    message_id = _insert_message(
        session,
        conversation_id=conversation.id,
        direction=WaDirection.INBOUND,
        author=WaAuthor.CONTACT,
        message_type=message.message_type,
        body=message.text or None,
        wa_message_id=message.wamid,
        status=WaMessageStatus.RECEIVED,
        status_at=message.timestamp or now,
        reply_payload=message.payload or None,
        **_media_values(message),
    )
    if message_id is None:
        return None
    conversations.on_inbound(conversation, message_id, message.timestamp or now)
    return message_id


def record_echo(session: Session, echo: WaIncoming, now: datetime) -> bool:
    """A message the studio sent from the WhatsApp Business app on the phone (coexistence):
    an operator's message, and the conversation goes to a human. False for a duplicate."""
    conversation = contact_conversation(session, echo.wa_id)
    message_id = _insert_message(
        session,
        conversation_id=conversation.id,
        direction=WaDirection.OUTBOUND,
        author=WaAuthor.OPERATOR,
        message_type=echo.message_type,
        body=echo.text or None,
        wa_message_id=echo.wamid,
        status=WaMessageStatus.SENT,
        status_at=echo.timestamp or now,
        **_media_values(echo),
    )
    if message_id is None:
        return False
    if conversation.status != conversations.HUMAN:
        logger.info("Conversation %s: message from the phone app, now with a human",
                    conversation.id)  # fmt: skip
    conversations.taken_by_operator(conversation)
    return True


def record_status(session: Session, status: WaStatus, now: datetime) -> bool:
    """Updates our message's status (never backwards). False if the wamid is unknown or the
    status changes nothing."""
    message = session.scalar(
        select(WaMessage).where(WaMessage.wa_message_id == status.wamid).with_for_update()
    )
    if message is None:
        logger.debug("Status %s of an unknown message ignored", status.status)
        return False
    try:
        new = _STATUS_ALIASES.get(status.status) or WaMessageStatus(status.status)
    except ValueError:
        logger.info("Message %s: unknown status %r ignored", message.id, status.status)
        return False
    current = message.status
    if new == WaMessageStatus.FAILED:
        if current in (WaMessageStatus.DELIVERED, WaMessageStatus.READ):
            return False
        message.error_code = status.error_code
        message.error_text = status.error_text
        logger.warning("Message %s failed in WhatsApp (code %s)", message.id, status.error_code)
    elif current == WaMessageStatus.FAILED or _RANK.get(new, 0) <= _rank(current):
        return False
    message.status = new
    message.status_at = status.timestamp or now
    return True
