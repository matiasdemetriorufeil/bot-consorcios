"""Chatwoot as a channel of app.channels.processor (Chatwoot's Agent Bot).

- With the bot = conversation "pending". Once open (handed off or taken by an operator) the
  bot neither answers nor hands off again.
- Who writes: the contact's phone only counts in a trusted inbox
  (CHATWOOT_TRUSTED_PHONE_INBOX_IDS, in production only WhatsApp, where Meta sets it). In any
  other inbox the person is unknown and uses a per-contact internal number for the email
  verification (web_contact_phone).
- History: the last 20 messages read from Chatwoot (app.chatwoot.history).
- Order: Chatwoot sends each message to WhatsApp in its own job and does not keep their order.
  In WhatsApp each part of a split turn is posted once the previous one went out (Chatwoot
  stored its WhatsApp id, client.message_sent), waiting SENT_WAIT_SECONDS at most.
- Options: an "input_select" message, shown as buttons or a list in WhatsApp and the web widget
  (INTERACTIVE_CHANNELS). Chatwoot answers before sending to WhatsApp, so a rejection by Meta
  (after our checks of app.bot.choices, unlikely) only shows in the Chatwoot panel. A widget
  tap arrives as message_updated (app.chatwoot.events): the history then includes the message
  with the options.
- Handoff: private note, labels and status "open" (Chatwoot's bot handoff).
"""

import logging
import time
from collections.abc import Callable

from app.bot.choices import Choice
from app.bot.identity import Identity, to_e164
from app.bot.tools import Handoff
from app.channels.base import InboundMessage
from app.channels.handoff import contact_attributes, handoff_labels, handoff_note
from app.channels.locks import CHATWOOT_LOCK_NAMESPACE
from app.chatwoot.client import ChatwootClient, ChatwootError
from app.chatwoot.events import IncomingMessage
from app.chatwoot.history import build_history
from app.llm import Message

logger = logging.getLogger(__name__)

# Internal numbers for contacts of untrusted inboxes: +54 9 11 09xxxxxx. Buenos Aires local
# numbers never start with 0, so they are not real lines, and they differ from the
# +5491100000xxx used by scripts/chat_cli.py.
WEB_CONTACT_PHONE_PREFIX = "+5491109"
MAX_WEB_CONTACT_ID = 999_999

# Inboxes where Chatwoot shows "input_select" messages as options to tap.
INTERACTIVE_CHANNELS = frozenset({"Channel::Whatsapp", "Channel::WebWidget"})
# Inboxes where Chatwoot sends each message in its own job (order not kept): before the next
# part of a split turn, wait until the previous one went out, at most SENT_WAIT_SECONDS.
WAIT_SENT_CHANNELS = frozenset({"Channel::Whatsapp"})
SENT_WAIT_SECONDS = 5.0
SENT_POLL_SECONDS = 0.5


def web_contact_phone(contact_id: int | None) -> str | None:
    """The internal number of a contact from an untrusted inbox (None if it does not fit)."""
    if contact_id is None or not 0 < contact_id <= MAX_WEB_CONTACT_ID:
        return None
    return f"{WEB_CONTACT_PHONE_PREFIX}{contact_id:06d}"


def resolve_phone(message: IncomingMessage, trusted_inbox_ids: list[int]) -> tuple[str, bool]:
    """(phone to identify with, whether it is the contact's real phone from a trusted inbox).
    Never trusts the phone of an inbox outside CHATWOOT_TRUSTED_PHONE_INBOX_IDS."""
    if (
        message.inbox_id in trusted_inbox_ids
        and message.contact_phone
        and to_e164(message.contact_phone)
    ):
        return message.contact_phone, True
    return web_contact_phone(message.contact_id) or "", False


def _source(message: InboundMessage) -> IncomingMessage:
    if not isinstance(message.source, IncomingMessage):
        raise TypeError("not a Chatwoot message")
    return message.source


class ChatwootChannel:
    name = "chatwoot"
    lock_namespace = CHATWOOT_LOCK_NAMESPACE

    def __init__(
        self,
        client: ChatwootClient,
        trusted_inbox_ids: list[int],
        *,
        sleep: Callable[[float], object] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.trusted_inbox_ids = trusted_inbox_ids
        self._sleep = sleep
        self._clock = clock

    def to_inbound(self, message: IncomingMessage) -> InboundMessage:
        phone, trusted = resolve_phone(message, self.trusted_inbox_ids)
        if not phone:
            logger.warning("Contact %s has no usable phone", message.contact_id)
        return InboundMessage(
            conversation_id=message.conversation_id,
            message_id=message.message_id,
            phone=phone,
            phone_trusted=trusted,
            content=message.content,
            content_type=message.content_type,
            attachment_types=message.attachment_types,
            source=message,
        )

    def still_with_bot(self, message: InboundMessage) -> bool:
        return self.client.get_conversation(message.conversation_id).get("status") == "pending"

    def history(self, message: InboundMessage) -> list[Message]:
        incoming = _source(message)
        if not self.client.can_read_history:
            logger.warning("CHATWOOT_API_TOKEN missing: answering without history")
            return []
        # A widget tap: the message with the options (its id) is part of the history.
        before = incoming.message_id + 1 if incoming.selection_of else incoming.message_id
        try:
            raw = self.client.get_messages(incoming.conversation_id, before=before)
        except ChatwootError as exc:
            logger.warning(
                "History of conversation %s unavailable: %s", incoming.conversation_id, exc
            )
            return []
        return build_history(raw, before_id=before, pending_selection=incoming.selection_of)

    def supports_choices(self, message: InboundMessage) -> bool:
        return _source(message).channel in INTERACTIVE_CHANNELS

    def send_text(self, message: InboundMessage, text: str, *, more: bool) -> None:
        created = self.client.send_message(message.conversation_id, text)
        if more and _source(message).channel in WAIT_SENT_CHANNELS:
            self._wait_sent(message.conversation_id, int(created["id"]))

    def send_choices(self, message: InboundMessage, text: str, choices: tuple[Choice, ...]) -> None:
        self.client.send_choices(message.conversation_id, text, choices)

    def _wait_sent(self, conversation_id: int, message_id: int) -> None:
        """Until Chatwoot sent the message to the channel, SENT_WAIT_SECONDS at most. Never
        raises: at worst the next part goes out without waiting."""
        if not self.client.can_read_history:
            logger.warning("CHATWOOT_API_TOKEN missing: next part sent without waiting")
            return
        deadline = self._clock() + SENT_WAIT_SECONDS
        while True:
            try:
                if self.client.message_sent(conversation_id, message_id):
                    return
            except ChatwootError as exc:
                logger.warning(
                    "Conversation %s: state of message %s unknown (%s), not waiting",
                    conversation_id, message_id, exc,
                )  # fmt: skip
                return
            if self._clock() >= deadline:
                logger.warning(
                    "Conversation %s: message %s not sent after %.0f s, sending the next part",
                    conversation_id, message_id, SENT_WAIT_SECONDS,
                )  # fmt: skip
                return
            self._sleep(SENT_POLL_SECONDS)

    def hand_off(
        self, message: InboundMessage, handoff: Handoff, who: Identity, trusted: bool
    ) -> None:
        conversation_id = message.conversation_id
        # Note and labels first: toggling to open is what tells operators to look.
        for step, call in (
            ("note", lambda: self.client.add_private_note(
                conversation_id, handoff_note(handoff, who, phone_trusted=trusted))),
            ("labels", lambda: self.client.add_labels(
                conversation_id, handoff_labels(handoff, who))),
        ):  # fmt: skip
            try:
                call()
            except ChatwootError as exc:
                logger.warning(
                    "Handoff %s failed for conversation %s: %s", step, conversation_id, exc
                )
        self.client.toggle_status(conversation_id, "open")
        logger.info(
            "Conversation %s: Chatwoot labels %s",
            conversation_id, ", ".join(handoff_labels(handoff, who)),
        )  # fmt: skip

    def after_turn(self, message: InboundMessage, who: Identity) -> None:
        """The contact's custom attributes (unit, building, verified)."""
        incoming = _source(message)
        if incoming.contact_id is None or not self.client.can_read_history:
            return
        wanted = contact_attributes(who)
        current = incoming.contact_attributes
        if not who.known and current.get("verified") is not True:
            return  # nothing to say about unknown contacts that were never verified
        if all(current.get(k) == v for k, v in wanted.items()):
            return
        try:
            self.client.update_contact_attributes(incoming.contact_id, wanted)
        except ChatwootError as exc:
            logger.warning("Contact %s not updated: %s", incoming.contact_id, exc)
