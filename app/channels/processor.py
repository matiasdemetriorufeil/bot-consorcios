"""Answers one incoming message, whatever the channel (runs in the background, after the
webhook's 200). The channel (app.channels.base.Channel) does what depends on it.

1. The conversation must still be with the bot (Channel.still_with_bot). Once a human has it
   (handed off or taken by an operator) the bot neither answers nor hands off again.
2. Who writes: the channel gives the phone and whether it set it itself (trusted).
3. Known people of a building outside the pilot go straight to a human.
4. Audio, stickers, locations... get a fixed "escribilo" reply; images and files a fixed
   thanks. Text goes to the agent with the channel's history.
5. The reply is sent only if the conversation is still with the bot (checked again by the
   channel right before each message: if a human took it meanwhile, ConversationTakenError
   drops the rest of the turn, with no handoff), as ONE message: the debt
   blocks built by the code (as is) and the agent's text at the end. Only a turn longer than
   outgoing.MAX_MESSAGE is split (app.channels.outgoing), and its parts go in order (the
   channel makes each one go out before the next: Channel.send_text(more=True)). Then the
   handoff, if any (Channel.hand_off).
6. A reply with options (offer_choices) goes as buttons or a list where the channel can
   (Channel.supports_choices), with the blocks in its text when they fit (otherwise the blocks
   go first, as text). Elsewhere, or if the channel rejects it, the options go numbered.

7. If the channel does not allow free messages any more (WhatsApp's 24-hour window,
   WindowClosedError), the conversation goes to a human with the reason window_closed.

Messages of one conversation are processed one at a time: a lock in this process plus a
Postgres advisory lock (app.channels.locks), so it also holds with several workers.
"""

import logging
import threading
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
from app.channels.base import (
    VIEWABLE_ATTACHMENTS,
    Channel,
    ChannelError,
    ConversationTakenError,
    InboundMessage,
    WindowClosedError,
)
from app.channels.locks import ConversationLock, no_lock
from app.channels.outgoing import pack, with_buttons
from app.config import Settings
from app.db.models import BotEvent, Building, PersonRole, Unit

logger = logging.getLogger(__name__)

UNSUPPORTED_REPLY = (
    "Todavía no puedo escuchar audios ni ver stickers, ubicaciones o videos. "
    "¿Me lo escribís, por favor?"
)
ATTACHMENT_REPLY = (
    "¡Gracias! Lo recibí, pero yo no puedo ver imágenes ni archivos. Si te paso con una "
    "persona del estudio, ella sí lo va a ver. ¿Me contás por escrito qué necesitás?"
)
NON_PILOT_GREETING = "Hola, soy el asistente automático del Estudio Diego Rufeil."

_LOCKS = [threading.Lock() for _ in range(64)]


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


class BotProcessor:
    def __init__(
        self,
        channel: Channel,
        session_factory: Callable[[], AbstractContextManager[Session]],
        agent_factory: Callable[[], Agent],
        settings: Settings,
        *,
        now: Callable[[], datetime] | None = None,
        warm_up: Callable[[], object] = lambda: None,
        lock: ConversationLock = no_lock,
    ) -> None:
        """warm_up: logs in to ConsorPlus in the background (app.sync.live.warm_up); called
        when an identified owner writes, so a debt query finds the session ready.
        lock: the cross-process lock (app.channels.locks.advisory_lock in production)."""
        self.channel = channel
        self.session_factory = session_factory
        self._agent_factory = agent_factory
        self._agent: Agent | None = None
        self.settings = settings
        self._warm_up = warm_up
        self._lock = lock
        tz = ZoneInfo(settings.timezone)
        self._now = now or (lambda: datetime.now(tz))

    @property
    def agent(self) -> Agent:
        if self._agent is None:
            self._agent = self._agent_factory()
        return self._agent

    def handle(self, message: InboundMessage) -> None:
        """Entry point of the background task: never raises."""
        conversation_id = message.conversation_id
        local = _LOCKS[conversation_id % len(_LOCKS)]
        try:
            with (
                local,
                self._lock(self.channel.lock_namespace, conversation_id),
                self.session_factory() as session,
            ):
                try:
                    self._handle(session, message)
                except WindowClosedError:
                    session.rollback()
                    logger.warning(
                        "Conversation %s: outside the channel's window, handing off",
                        conversation_id,
                    )
                    self._window_closed_handoff(session, message)
                except Exception:
                    session.rollback()
                    logger.exception("%s message %s failed", self.channel.name, message.message_id)
                    self._emergency_handoff(session, message)
        except Exception:
            # The lock itself failed (database down): nothing else can be done here.
            logger.exception("Message %s could not be processed", message.message_id)

    # --- Steps --------------------------------------------------------------------------

    def _handle(self, session: Session, message: InboundMessage) -> None:
        conversation_id = message.conversation_id
        if not self.channel.still_with_bot(message):
            logger.info("Conversation %s no longer with the bot: message %s skipped",
                        conversation_id, message.message_id)  # fmt: skip
            self._log(session, message, None, "skipped", reason="not_pending")
            return
        phone, trusted = message.phone, message.phone_trusted
        who = identify_by_phone(session, phone) if phone else Identity()
        logger.info(
            "Processing %s message %s of conversation %s (trusted phone: %s, known: %s)",
            self.channel.name, message.message_id, conversation_id,
            "yes" if trusted else "no", "yes" if who.known else "no",
        )  # fmt: skip

        if who.known and not in_pilot(session, who):
            logger.info("Conversation %s: building outside the pilot, handing off", conversation_id)
            notice = f"{NON_PILOT_GREETING} {self._notice(session)}"
            handoff = Handoff("non_pilot", "Propietario de un edificio fuera de la prueba piloto.")
            self._log(session, message, phone, "handoff", channel_event=False,
                      **_handoff_payload(handoff))  # fmt: skip
            self._finish(session, message, notice, handoff)
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
            self._log(session, message, phone, "fixed_reply", kind="attachment")
            self._finish(session, message, reply, None)
            return
        if message.attachment_types:
            text += _attachment_note(message.attachment_types)

        if not phone:
            # Unknown with no way to verify; the agent still answers.
            logger.warning("Message %s has no usable phone", message.message_id)
        answer = self.agent.reply(
            session,
            phone,
            text,
            self.channel.history(message),
            conversation_id=conversation_id,
        )
        self._finish(
            session,
            message,
            answer.text,
            answer.handoff,
            answer.debt_messages,
            answer.choices,
        )

    def _finish(
        self,
        session: Session,
        message: InboundMessage,
        reply: str,
        handoff: Handoff | None,
        debt_messages: list[str] | None = None,
        choices: tuple[Choice, ...] = (),
    ) -> None:
        """Send the debt blocks and the reply (if still with the bot), carry out the
        handoff, let the channel update the rest."""
        conversation_id = message.conversation_id
        phone = message.phone
        if not self.channel.still_with_bot(message):
            # An operator took the conversation while the bot was thinking.
            logger.info(
                "Conversation %s taken by a human meanwhile: reply dropped", conversation_id
            )
            self._log(session, message, phone, "reply_dropped")
            return
        try:
            sent = self._send_reply(message, debt_messages or [], reply, choices)
        except ConversationTakenError:
            logger.info(
                "Conversation %s taken by a human while sending: rest of the turn dropped",
                conversation_id,
            )
            self._log(session, message, phone, "reply_dropped", while_sending=True)
            return
        logger.info(
            "Conversation %s: reply sent in %d message(s) (%d debt block(s), %d option(s))",
            conversation_id, sent, len(debt_messages or []), len(choices),
        )  # fmt: skip
        who = identify_by_phone(session, phone) if phone else Identity()  # may have verified
        if handoff is not None:
            try:
                self._hand_off(message, handoff, who, message.phone_trusted)
            except ChannelError:
                # The reply already went out: no fallback message, just make it visible.
                logger.exception("Handoff of conversation %s failed", conversation_id)
                self._log(session, message, phone, "handoff_failed")
        self.channel.after_turn(message, who)

    def _send_reply(
        self,
        message: InboundMessage,
        blocks: list[str],
        reply: str,
        choices: tuple[Choice, ...],
    ) -> int:
        """Sends the turn (see the module doc); returns how many messages it took."""
        conversation_id = message.conversation_id
        if choices and self.channel.supports_choices(message):
            first, text = with_buttons(blocks, reply)
            sent = self._send_texts(message, first, more=True)
            try:
                self.channel.send_choices(message, text, choices)
                return sent + 1
            except (ConversationTakenError, WindowClosedError):
                raise
            except (ChannelError, ValueError) as exc:
                logger.warning(
                    "Conversation %s: options not sent as buttons (%s), sending them numbered",
                    conversation_id, exc,
                )  # fmt: skip
            if first:
                blocks = []  # already sent before the buttons
            numbered = numbered_text(reply, [c.title for c in choices])
            return sent + self._send_texts(message, pack([*blocks, numbered]))
        if choices:
            reply = numbered_text(reply, [c.title for c in choices])
        return self._send_texts(message, pack([*blocks, reply]))

    def _send_texts(self, message: InboundMessage, texts: list[str], *, more: bool = False) -> int:
        """One after the other. more: a message that is not in texts follows them."""
        for index, text in enumerate(texts):
            self.channel.send_text(message, text, more=more or index < len(texts) - 1)
        return len(texts)

    def _hand_off(
        self, message: InboundMessage, handoff: Handoff, who: Identity, trusted: bool
    ) -> None:
        self.channel.hand_off(message, handoff, who, trusted)
        logger.info(
            "Conversation %s handed off (reason: %s, priority: %s)",
            message.conversation_id, handoff.reason, handoff.priority,
        )  # fmt: skip

    def _emergency_handoff(self, session: Session, message: InboundMessage) -> None:
        """Something broke: tell the person and leave the conversation to a human."""
        try:
            if not self.channel.still_with_bot(message):
                return
            notice = self._notice(session)
            try:
                self.channel.send_text(message, f"{FALLBACK_REPLY} {notice}", more=False)
            except ChannelError as exc:
                # E.g. WhatsApp's 24-hour window closed: a human must see it anyway.
                logger.warning(
                    "Conversation %s: fallback message not sent (%s)", message.conversation_id, exc
                )
            handoff = Handoff("technical_error", "El bot falló al procesar el mensaje.")
            self._hand_off(message, handoff, Identity(), trusted=False)
            self._log(session, message, None, "handoff", channel_event=False,
                      **_handoff_payload(handoff))  # fmt: skip
        except Exception:
            logger.exception(
                "Emergency handoff failed for conversation %s", message.conversation_id
            )

    def _window_closed_handoff(self, session: Session, message: InboundMessage) -> None:
        """The reply cannot go out (24-hour window): no fallback message either, the
        conversation goes to a human with its own reason."""
        try:
            if not self.channel.still_with_bot(message):
                return
            phone = message.phone
            who = identify_by_phone(session, phone) if phone else Identity()
            handoff = Handoff(
                "window_closed",
                "La ventana de 24 h de WhatsApp estaba cerrada: el bot no pudo responder.",
            )
            self._hand_off(message, handoff, who, message.phone_trusted)
            self._log(session, message, phone, "handoff", channel_event=False,
                      **_handoff_payload(handoff))  # fmt: skip
        except Exception:
            logger.exception(
                "Window-closed handoff failed for conversation %s", message.conversation_id
            )

    # --- Helpers ------------------------------------------------------------------------

    def _notice(self, session: Session) -> str:
        cfg = load_bot_config(session, self.settings)
        return handoff_notice(self._now(), *cfg.hours, cfg.out_of_hours_text)

    def _log(
        self,
        session: Session,
        message: InboundMessage,
        phone: str | None,
        event_type: str,
        *,
        channel_event: bool = True,
        **payload: object,
    ) -> None:
        """channel_event: the event is the channel's own (prefixed: chatwoot_skipped...)."""
        session.add(
            BotEvent(
                conversation_id=message.conversation_id,
                phone_e164=to_e164(phone) if phone else None,
                event_type=f"{self.channel.name}_{event_type}" if channel_event else event_type,
                payload={"message_id": message.message_id, **payload},
            )
        )
        session.commit()


def _handoff_payload(handoff: Handoff) -> dict[str, str]:
    return {"reason": handoff.reason, "summary": handoff.summary, "priority": handoff.priority}
