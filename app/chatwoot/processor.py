"""Answers one incoming Chatwoot message (runs in the background, after the webhook's 200).

1. The conversation must still be "pending" (with the bot). Once open (handed off or taken
   by an operator) the bot neither answers nor hands off again.
2. Who writes: the contact's phone only counts in a trusted inbox
   (CHATWOOT_TRUSTED_PHONE_INBOX_IDS, in production only WhatsApp, where Meta sets it). In
   any other inbox the person is unknown and uses a per-contact internal number for the
   email verification (web_contact_phone).
3. Known people of a building outside the pilot go straight to a human.
4. Audio, stickers, locations... get a fixed "escribilo" reply; images and files a fixed
   thanks. Text goes to the agent with the last 20 messages read from Chatwoot.
5. The reply is sent only if the conversation is still pending, as ONE message: the debt
   blocks built by the code (as is) and the agent's text at the end. Chatwoot sends each
   message to WhatsApp in its own job and does not keep their order, so they cannot go apart.
   Only a turn longer than outgoing.MAX_MESSAGE is split (app.chatwoot.outgoing). In WhatsApp
   each part is posted once the previous one went out (Chatwoot stored its WhatsApp id,
   client.message_sent), waiting SENT_WAIT_SECONDS at most, so they arrive in order. Then
   the handoff, if any: private note, labels and status "open" (Chatwoot's bot handoff).
6. A reply with options (offer_choices) goes as buttons or a list in WhatsApp and the web
   widget (INTERACTIVE_CHANNELS), with the blocks in its text when they fit (otherwise the
   blocks go first, as text). In any other inbox, or if Chatwoot rejects it, the options
   go numbered in the text. Chatwoot answers before sending to WhatsApp, so a rejection by
   Meta (after our checks of app.bot.choices, unlikely) only shows in the Chatwoot panel.
   A widget tap arrives as message_updated (app.chatwoot.events): the history then includes
   the message with the options.

Messages of one conversation are processed one at a time (lock per conversation, in this
process).
"""

import logging
import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot.agent import FALLBACK_REPLY, Agent
from app.bot.bot_config import load_bot_config
from app.bot.choices import Choice, numbered_text
from app.bot.identity import Identity, identify_by_phone, to_e164
from app.bot.prompts import handoff_notice
from app.bot.tools import Handoff
from app.chatwoot.client import ChatwootClient, ChatwootError
from app.chatwoot.events import VIEWABLE_ATTACHMENTS, IncomingMessage
from app.chatwoot.handoff import contact_attributes, handoff_labels, handoff_note
from app.chatwoot.history import build_history
from app.chatwoot.outgoing import pack, with_buttons
from app.config import Settings
from app.db.models import BotEvent, Building, PersonRole, Unit
from app.llm import Message

logger = logging.getLogger(__name__)

# Internal numbers for contacts of untrusted inboxes: +54 9 11 09xxxxxx. Buenos Aires local
# numbers never start with 0, so they are not real lines, and they differ from the
# +5491100000xxx used by scripts/chat_cli.py.
WEB_CONTACT_PHONE_PREFIX = "+5491109"
MAX_WEB_CONTACT_ID = 999_999

UNSUPPORTED_REPLY = (
    "Todavía no puedo escuchar audios ni ver stickers, ubicaciones o videos. "
    "¿Me lo escribís, por favor?"
)
ATTACHMENT_REPLY = (
    "¡Gracias! Lo recibí, pero yo no puedo ver imágenes ni archivos. Si te paso con una "
    "persona del estudio, ella sí lo va a ver. ¿Me contás por escrito qué necesitás?"
)
NON_PILOT_GREETING = "Hola, soy el asistente automático del Estudio Diego Rufeil."
# Inboxes where Chatwoot shows "input_select" messages as options to tap.
INTERACTIVE_CHANNELS = frozenset({"Channel::Whatsapp", "Channel::WebWidget"})
# Inboxes where Chatwoot sends each message in its own job (order not kept): before the next
# part of a split turn, wait until the previous one went out, at most SENT_WAIT_SECONDS.
WAIT_SENT_CHANNELS = frozenset({"Channel::Whatsapp"})
SENT_WAIT_SECONDS = 5.0
SENT_POLL_SECONDS = 0.5

_LOCKS = [threading.Lock() for _ in range(64)]


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


def in_pilot(session: Session, who: Identity) -> bool:
    """Whether any of the person's units is in a pilot building."""
    unit_ids = [u.unit_id for u in who.units]
    if not unit_ids:
        return False
    pilots = session.scalars(
        select(Building.pilot)
        .join(Unit, Unit.building_id == Building.id)
        .where(Unit.id.in_(unit_ids))
    )
    return any(pilots)


def _attachment_note(types: tuple[str, ...]) -> str:
    kinds = ", ".join(sorted(set(types)))
    return (
        f"\n\n[La persona además mandó un adjunto ({kinds}) que vos no podés ver ni escuchar; "
        "si derivás, el estudio sí lo ve.]"
    )


class ChatwootBot:
    def __init__(
        self,
        client: ChatwootClient,
        session_factory: Callable[[], AbstractContextManager[Session]],
        agent_factory: Callable[[], Agent],
        settings: Settings,
        *,
        now: Callable[[], datetime] | None = None,
        warm_up: Callable[[], object] = lambda: None,
        sleep: Callable[[float], object] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """warm_up: logs in to ConsorPlus in the background (app.sync.live.warm_up); called
        when an identified owner writes, so a debt query finds the session ready."""
        self.client = client
        self.session_factory = session_factory
        self._agent_factory = agent_factory
        self._agent: Agent | None = None
        self.settings = settings
        self._warm_up = warm_up
        tz = ZoneInfo(settings.timezone)
        self._now = now or (lambda: datetime.now(tz))
        self._sleep = sleep
        self._clock = clock

    @property
    def agent(self) -> Agent:
        if self._agent is None:
            self._agent = self._agent_factory()
        return self._agent

    def handle(self, message: IncomingMessage) -> None:
        """Entry point of the background task: never raises."""
        lock = _LOCKS[message.conversation_id % len(_LOCKS)]
        with lock, self.session_factory() as session:
            try:
                self._handle(session, message)
            except Exception:
                session.rollback()
                logger.exception("Chatwoot message %s failed", message.message_id)
                self._emergency_handoff(session, message)

    # --- Steps --------------------------------------------------------------------------

    def _handle(self, session: Session, message: IncomingMessage) -> None:
        conversation_id = message.conversation_id
        if not self._still_pending(conversation_id):
            logger.info("Conversation %s no longer pending: message %s skipped",
                        conversation_id, message.message_id)  # fmt: skip
            self._log(session, message, None, "chatwoot_skipped", reason="not_pending")
            return
        phone, trusted = resolve_phone(message, self.settings.chatwoot_trusted_phone_inbox_ids)
        who = identify_by_phone(session, phone) if phone else Identity()
        logger.info(
            "Processing message %s of conversation %s (inbox %s, trusted phone: %s, known: %s)",
            message.message_id, conversation_id, message.inbox_id,
            "yes" if trusted else "no", "yes" if who.known else "no",
        )  # fmt: skip

        if who.known and not in_pilot(session, who):
            logger.info("Conversation %s: building outside the pilot, handing off", conversation_id)
            notice = f"{NON_PILOT_GREETING} {self._notice(session)}"
            handoff = Handoff("non_pilot", "Propietario de un edificio fuera de la prueba piloto.")
            self._log(session, message, phone, "handoff", **_handoff_payload(handoff))
            self._finish(session, message, phone, trusted, notice, handoff)
            return
        if any(u.role == PersonRole.OWNER for u in who.units):
            self._warm_up()  # they may ask for their debt: log in while the LLM thinks

        text = message.content
        if not text:
            viewable = any(t in VIEWABLE_ATTACHMENTS for t in message.attachment_types)
            reply = ATTACHMENT_REPLY if viewable and not message.is_sticker else UNSUPPORTED_REPLY
            kinds = ", ".join(message.attachment_types) or message.content_type
            logger.info(
                "Conversation %s: attachment only (%s), fixed reply", conversation_id, kinds
            )
            self._log(session, message, phone, "chatwoot_fixed_reply", kind="attachment")
            self._finish(session, message, phone, trusted, reply, None)
            return
        if message.attachment_types:
            text += _attachment_note(message.attachment_types)

        if not phone:
            # Contact id out of range: unknown with no way to verify; the agent still answers.
            logger.warning("Contact %s has no usable phone", message.contact_id)
        answer = self.agent.reply(
            session,
            phone,
            text,
            self._history(message),
            conversation_id=conversation_id,
        )
        self._finish(
            session,
            message,
            phone,
            trusted,
            answer.text,
            answer.handoff,
            answer.debt_messages,
            answer.choices,
        )

    def _finish(
        self,
        session: Session,
        message: IncomingMessage,
        phone: str,
        trusted: bool,
        reply: str,
        handoff: Handoff | None,
        debt_messages: list[str] | None = None,
        choices: tuple[Choice, ...] = (),
    ) -> None:
        """Send the debt blocks and the reply (if still pending), carry out the handoff,
        update the contact."""
        conversation_id = message.conversation_id
        if not self._still_pending(conversation_id):
            # An operator took the conversation while the bot was thinking.
            logger.info(
                "Conversation %s taken by a human meanwhile: reply dropped", conversation_id
            )
            self._log(session, message, phone, "chatwoot_reply_dropped")
            return
        sent = self._send_reply(message, debt_messages or [], reply, choices)
        logger.info(
            "Conversation %s: reply sent in %d message(s) (%d debt block(s), %d option(s))",
            conversation_id, sent, len(debt_messages or []), len(choices),
        )  # fmt: skip
        who = identify_by_phone(session, phone) if phone else Identity()  # may have verified
        if handoff is not None:
            try:
                self._hand_off(conversation_id, handoff, who, trusted)
            except ChatwootError:
                # The reply already went out: no fallback message, just make it visible.
                logger.exception("Handoff of conversation %s failed", conversation_id)
                self._log(session, message, phone, "chatwoot_handoff_failed")
        self._update_contact(message, who)

    def _send_reply(
        self,
        message: IncomingMessage,
        blocks: list[str],
        reply: str,
        choices: tuple[Choice, ...],
    ) -> int:
        """Sends the turn (see the module doc); returns how many messages it took."""
        conversation_id = message.conversation_id
        wait = message.channel in WAIT_SENT_CHANNELS
        if choices and message.channel in INTERACTIVE_CHANNELS:
            first, text = with_buttons(blocks, reply)
            sent = self._send_texts(conversation_id, first, wait=wait, more=True)
            try:
                self.client.send_choices(conversation_id, text, choices)
                return sent + 1
            except (ChatwootError, ValueError) as exc:
                logger.warning(
                    "Conversation %s: options not sent as buttons (%s), sending them numbered",
                    conversation_id, exc,
                )  # fmt: skip
            if first:
                blocks = []  # already sent before the buttons
            numbered = numbered_text(reply, [c.title for c in choices])
            return sent + self._send_texts(conversation_id, pack([*blocks, numbered]), wait=wait)
        if choices:
            reply = numbered_text(reply, [c.title for c in choices])
        return self._send_texts(conversation_id, pack([*blocks, reply]), wait=wait)

    def _send_texts(
        self, conversation_id: int, texts: list[str], *, wait: bool, more: bool = False
    ) -> int:
        """One after the other. With wait, each one that has a message after it (more: one
        that is not in texts) must have gone out before the next is posted."""
        for index, text in enumerate(texts):
            created = self.client.send_message(conversation_id, text)
            if wait and (more or index < len(texts) - 1):
                self._wait_sent(conversation_id, int(created["id"]))
        return len(texts)

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

    def _hand_off(
        self, conversation_id: int, handoff: Handoff, who: Identity, trusted: bool
    ) -> None:
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
            "Conversation %s handed off (reason: %s, priority: %s, labels: %s)",
            conversation_id, handoff.reason, handoff.priority,
            ", ".join(handoff_labels(handoff, who)),
        )  # fmt: skip

    def _emergency_handoff(self, session: Session, message: IncomingMessage) -> None:
        """Something broke: tell the person and leave the conversation to a human."""
        try:
            if not self._still_pending(message.conversation_id):
                return
            notice = self._notice(session)
            self.client.send_message(message.conversation_id, f"{FALLBACK_REPLY} {notice}")
            handoff = Handoff("technical_error", "El bot falló al procesar el mensaje.")
            self._hand_off(message.conversation_id, handoff, Identity(), trusted=False)
            self._log(session, message, None, "handoff", **_handoff_payload(handoff))
        except Exception:
            logger.exception(
                "Emergency handoff failed for conversation %s", message.conversation_id
            )

    # --- Helpers ------------------------------------------------------------------------

    def _still_pending(self, conversation_id: int) -> bool:
        return self.client.get_conversation(conversation_id).get("status") == "pending"

    def _notice(self, session: Session) -> str:
        cfg = load_bot_config(session, self.settings)
        return handoff_notice(self._now(), *cfg.hours, cfg.out_of_hours_text)

    def _history(self, message: IncomingMessage) -> list[Message]:
        if not self.client.can_read_history:
            logger.warning("CHATWOOT_API_TOKEN missing: answering without history")
            return []
        # A widget tap: the message with the options (its id) is part of the history.
        before = message.message_id + 1 if message.selection_of else message.message_id
        try:
            raw = self.client.get_messages(message.conversation_id, before=before)
        except ChatwootError as exc:
            logger.warning(
                "History of conversation %s unavailable: %s", message.conversation_id, exc
            )
            return []
        return build_history(raw, before_id=before, pending_selection=message.selection_of)

    def _update_contact(self, message: IncomingMessage, who: Identity) -> None:
        if message.contact_id is None or not self.client.can_read_history:
            return
        wanted = contact_attributes(who)
        current = message.contact_attributes
        if not who.known and current.get("verified") is not True:
            return  # nothing to say about unknown contacts that were never verified
        if all(current.get(k) == v for k, v in wanted.items()):
            return
        try:
            self.client.update_contact_attributes(message.contact_id, wanted)
        except ChatwootError as exc:
            logger.warning("Contact %s not updated: %s", message.contact_id, exc)

    def _log(
        self,
        session: Session,
        message: IncomingMessage,
        phone: str | None,
        event_type: str,
        **payload: object,
    ) -> None:
        session.add(
            BotEvent(
                conversation_id=message.conversation_id,
                phone_e164=to_e164(phone) if phone else None,
                event_type=event_type,
                payload={"message_id": message.message_id, **payload},
            )
        )
        session.commit()


def _handoff_payload(handoff: Handoff) -> dict[str, str]:
    return {"reason": handoff.reason, "summary": handoff.summary, "priority": handoff.priority}
