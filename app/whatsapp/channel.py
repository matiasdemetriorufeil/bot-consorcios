"""The WhatsApp Cloud API as a channel of app.channels.processor (CHANNEL=whatsapp).

- With the bot = WaConversation.status "bot" (app.whatsapp.conversations).
- Who writes: "+" + the wa_id Meta gives, always trusted (Meta sets it and every webhook is
  signed with the app secret).
- History: the conversation's messages from the start of the bot's current stretch
  (bot_since_message_id), without internal notes; the bot's and the operators' messages are
  the assistant's. A message with options keeps their titles (app.bot.choices.with_options).
- 24-hour window: free text (and options) only within 24 h of the contact's last message;
  outside it only an approved template can go (WhatsAppClient.send_template).
- Order: each send waits for Meta's answer before the next one, so the parts of a turn go in
  order (there are no separate jobs as in Chatwoot).
- Every message sent is stored (author bot); its status comes later by webhook.
- Handoff: the conversation goes to "waiting_human" with the reason, priority, summary and
  labels, and the same note Chatwoot got is left as an internal note.
"""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot.choices import Choice, with_options
from app.bot.identity import Identity
from app.bot.tools import Handoff
from app.channels.base import InboundMessage
from app.channels.handoff import handoff_labels, handoff_note
from app.channels.history import to_history
from app.channels.locks import WHATSAPP_LOCK_NAMESPACE
from app.db.models import (
    WaAuthor,
    WaConversation,
    WaConversationStatus,
    WaDirection,
    WaMessage,
    WaMessageStatus,
)
from app.llm import Message
from app.whatsapp import conversations
from app.whatsapp.client import WhatsAppClient, WhatsAppError, WindowClosedError
from app.whatsapp.events import ATTACHMENT_KINDS

logger = logging.getLogger(__name__)

WINDOW = timedelta(hours=24)
# Rows read for the history (trim_history keeps the last 20 turns).
HISTORY_ROWS = 80


@dataclass(frozen=True)
class WaSource:
    message_id: int  # wa_messages.id of the incoming message
    wa_id: str


def _source(message: InboundMessage) -> WaSource:
    if not isinstance(message.source, WaSource):
        raise TypeError("not a WhatsApp message")
    return message.source


def history_text(message: WaMessage) -> str:
    """How a stored message reads in the agent's history."""
    text = (message.body or "").strip()
    if text and message.choices:
        return with_options(text, list(message.choices))
    if text:
        return text
    if kind := ATTACHMENT_KINDS.get(message.message_type):
        return f"[adjunto: {kind}]"
    return ""


class WhatsAppChannel:
    name = "whatsapp"
    lock_namespace = WHATSAPP_LOCK_NAMESPACE

    def __init__(
        self,
        client: WhatsAppClient,
        session_factory: Callable[[], AbstractContextManager[Session]],
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.client = client
        self.session_factory = session_factory
        self._now = now

    # --- State ----------------------------------------------------------------------------

    def still_with_bot(self, message: InboundMessage) -> bool:
        with self.session_factory() as session:
            status = session.scalar(
                select(WaConversation.status).where(WaConversation.id == message.conversation_id)
            )
        return status == WaConversationStatus.BOT

    def window_open(self, session: Session, conversation_id: int) -> bool:
        last = session.scalar(
            select(WaConversation.last_inbound_at).where(WaConversation.id == conversation_id)
        )
        return last is not None and self._now() - last < WINDOW

    def history(self, message: InboundMessage) -> list[Message]:
        source = _source(message)
        with self.session_factory() as session:
            since = session.scalar(
                select(WaConversation.bot_since_message_id).where(
                    WaConversation.id == message.conversation_id
                )
            )
            stmt = (
                select(WaMessage)
                .where(
                    WaMessage.conversation_id == message.conversation_id,
                    WaMessage.id < source.message_id,
                    WaMessage.is_internal_note.is_(False),
                )
                .order_by(WaMessage.id.desc())
                .limit(HISTORY_ROWS)
            )
            if since is not None:
                stmt = stmt.where(WaMessage.id >= since)
            rows = list(reversed(list(session.scalars(stmt))))
        return to_history((m.author == WaAuthor.CONTACT, history_text(m)) for m in rows)

    # --- Sending --------------------------------------------------------------------------

    def supports_choices(self, message: InboundMessage) -> bool:
        return True

    def send_text(self, message: InboundMessage, text: str, *, more: bool) -> None:
        self._send(message, "text", text, (), lambda to: self.client.send_text(to, text))

    def send_choices(self, message: InboundMessage, text: str, choices: tuple[Choice, ...]) -> None:
        self._send(
            message,
            "interactive",
            text,
            choices,
            lambda to: self.client.send_choices(to, text, choices),
        )

    def _send(
        self,
        message: InboundMessage,
        kind: str,
        text: str,
        choices: tuple[Choice, ...],
        send: Callable[[str], str],
    ) -> None:
        """Checks the window, sends and stores the message (also when Meta rejects it, with
        the error, so the panel shows what did not go out)."""
        source = _source(message)
        with self.session_factory() as session:
            if not self.window_open(session, message.conversation_id):
                raise WindowClosedError(
                    f"conversación {message.conversation_id}: fuera de la ventana de 24 h"
                )
            row = WaMessage(
                conversation_id=message.conversation_id,
                direction=WaDirection.OUTBOUND,
                author=WaAuthor.BOT,
                message_type=kind,
                body=text,
                choices=[c.title for c in choices] or None,
            )
            try:
                row.wa_message_id = send(source.wa_id)
                row.status = WaMessageStatus.SENT
            except WhatsAppError as exc:
                row.status = WaMessageStatus.FAILED
                row.error_code = exc.code
                row.error_text = str(exc)[:1000]
                session.add(row)
                session.commit()
                raise
            row.status_at = self._now()
            session.add(row)
            session.commit()

    # --- Handoff --------------------------------------------------------------------------

    def hand_off(
        self, message: InboundMessage, handoff: Handoff, who: Identity, trusted: bool
    ) -> None:
        now = self._now()
        with self.session_factory() as session:
            conversation = session.get(
                WaConversation, message.conversation_id, with_for_update=True
            )
            if conversation is None:
                raise WhatsAppError(f"conversación {message.conversation_id} inexistente")
            labels = handoff_labels(handoff, who)
            conversations.hand_off(
                conversation,
                reason=handoff.reason,
                priority=handoff.priority,
                summary=handoff.summary,
                labels=labels,
                at=now,
            )
            session.add(
                WaMessage(
                    conversation_id=conversation.id,
                    direction=WaDirection.OUTBOUND,
                    author=WaAuthor.SYSTEM,
                    message_type="note",
                    body=handoff_note(handoff, who, phone_trusted=trusted),
                    is_internal_note=True,
                )
            )
            session.commit()
        logger.info(
            "Conversation %s waiting for a human (labels: %s)",
            message.conversation_id, ", ".join(labels),
        )  # fmt: skip

    def after_turn(self, message: InboundMessage, who: Identity) -> None:
        return None
