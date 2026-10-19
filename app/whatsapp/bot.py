"""Answers a stored incoming WhatsApp message (the webhook's background task, or the recovery
after a restart).

1. Claim it: processing_started_at is set only if nobody did (an UPDATE ... RETURNING), so a
   message is taken by one worker only, even with several.
2. Download its attachment, if any (app.whatsapp.media): Meta's links expire in minutes.
3. Reactions, system notices and the like are only stored. Anything else goes to the
   processor (app.channels.processor), which answers only if the conversation is with the bot.
   A message that arrives out of order (WhatsApp says it was sent before another one of the
   same number we already got: Meta retrying a delivery that failed) is not answered: it is
   marked processed ("late_out_of_order") with an internal note for the studio, so the bot
   never answers an old "Hola" in the middle of a newer exchange. A message only delayed but
   still the newest is answered as usual.
4. processed_at is set at the end, whatever happened. A message claimed but never processed
   (the api restarted midway) is taken again by the recovery (recover_unanswered).

Recovery, on startup (app.main): messages of conversations with the bot younger than
WHATSAPP_RECOVERY_MINUTES are answered, oldest first; older ones are marked unanswered
(processing_note "stale_after_restart", a warning and a bot_events row), and those of
conversations a human has are just marked processed ("not_with_bot").
"""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.channels.base import InboundMessage
from app.channels.processor import BotProcessor
from app.db.models import (
    BotEvent,
    WaAuthor,
    WaContact,
    WaConversation,
    WaConversationStatus,
    WaDirection,
    WaMessage,
)
from app.whatsapp.channel import WaSource
from app.whatsapp.events import ATTACHMENT_KINDS, is_silent
from app.whatsapp.media import MediaStore

logger = logging.getLogger(__name__)

# A claim older than this without processed_at: its worker died, it can be taken again.
CLAIM_TIMEOUT = timedelta(minutes=5)
UNANSWERED_EVENT = "unanswered_after_restart"
LATE_NOTE = "late_out_of_order"  # processing_note
LATE_EVENT = "late_message_unanswered"
# The internal note it leaves (message_type), shown by the panel as such (app.admin.inbox).
LATE_NOTE_TYPE = "late_note"


@dataclass
class RecoveryResult:
    answered: list[int] = field(default_factory=list)
    stale: list[int] = field(default_factory=list)
    not_with_bot: list[int] = field(default_factory=list)


class WhatsAppBot:
    def __init__(
        self,
        processor: BotProcessor,
        session_factory: Callable[[], AbstractContextManager[Session]],
        media: MediaStore | None,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.processor = processor
        self.session_factory = session_factory
        self.media = media
        self._now = now

    def process(self, message_id: int) -> bool:
        """The webhook's background task: never raises. False if another worker has it."""
        try:
            return self._process(message_id)
        except Exception:
            logger.exception("WhatsApp message %s could not be processed", message_id)
            return False

    def _claim(self, session: Session, message_id: int, now: datetime) -> bool:
        claimed = session.execute(
            update(WaMessage)
            .where(
                WaMessage.id == message_id,
                WaMessage.direction == WaDirection.INBOUND,
                WaMessage.processed_at.is_(None),
                or_(
                    WaMessage.processing_started_at.is_(None),
                    WaMessage.processing_started_at < now - CLAIM_TIMEOUT,
                ),
            )
            .values(processing_started_at=now)
            .returning(WaMessage.id)
        ).scalar()
        session.commit()
        return claimed is not None

    def _process(self, message_id: int) -> bool:
        with self.session_factory() as session:
            if not self._claim(session, message_id, self._now()):
                logger.info("WhatsApp message %s already taken: skipped", message_id)
                return False
            message = session.get(WaMessage, message_id, populate_existing=True)
            if message is None:
                raise LookupError(message_id)
            if self.media is not None and message.media_id:
                self.media.fetch(message, self._now())
                session.commit()
            wa_id = session.scalar(
                select(WaContact.wa_id)
                .join(WaConversation, WaConversation.contact_id == WaContact.id)
                .where(WaConversation.id == message.conversation_id)
            )
            inbound = InboundMessage(
                conversation_id=message.conversation_id,
                message_id=message.id,
                phone=f"+{wa_id}",
                phone_trusted=True,
                content=(message.body or "").strip(),
                content_type="sticker" if message.message_type == "sticker" else "text",
                attachment_types=(
                    (ATTACHMENT_KINDS[message.message_type],)
                    if message.message_type in ATTACHMENT_KINDS
                    else ()
                ),
                payload=message.reply_payload or "",
                source=WaSource(message_id=message.id, wa_id=str(wa_id)),
            )
            kind = message.message_type
            silent = is_silent(kind, message.body)
            if not silent and self._out_of_order(session, message):
                self._leave_unanswered(session, message)
                return True
        note = None
        if silent:
            note = "silent"
            logger.info("WhatsApp message %s (%s): stored, no answer", message_id, kind)
        else:
            self.processor.handle(inbound)
        self._mark_processed(message_id, note)
        return True

    @staticmethod
    def _out_of_order(session: Session, message: WaMessage) -> bool:
        """A newer message of the same number (by WhatsApp's send time) already came in, and
        the conversation is with the bot."""
        if message.status_at is None:
            return False
        status = session.scalar(
            select(WaConversation.status).where(WaConversation.id == message.conversation_id)
        )
        if status != WaConversationStatus.BOT:
            return False
        newer = session.scalar(
            select(WaMessage.id)
            .where(
                WaMessage.conversation_id == message.conversation_id,
                WaMessage.direction == WaDirection.INBOUND,
                WaMessage.id < message.id,
                WaMessage.status_at > message.status_at,
            )
            .limit(1)
        )
        return newer is not None

    def _leave_unanswered(self, session: Session, message: WaMessage) -> None:
        """Not answered: processed with LATE_NOTE, an internal note and a bot_events row."""
        now = self._now()
        tz = ZoneInfo(self.processor.settings.timezone)
        sent = message.status_at.astimezone(tz)  # type: ignore[union-attr]
        came = now.astimezone(tz)
        text = f"«{message.body}»" if message.body else "(sin texto)"
        session.add(
            WaMessage(
                conversation_id=message.conversation_id,
                direction=WaDirection.OUTBOUND,
                author=WaAuthor.SYSTEM,
                message_type=LATE_NOTE_TYPE,
                body=(
                    f"El teléfono lo mandó el {sent:%d/%m} a las {sent:%H:%M}, pero llegó el "
                    f"{came:%d/%m} a las {came:%H:%M}, cuando ya había llegado un mensaje más "
                    "nuevo. El bot no lo contestó para no responder fuera de orden: revisalo "
                    f"por si trae algo importante.\nMensaje: {text}"
                ),
                is_internal_note=True,
            )
        )
        session.add(
            BotEvent(
                conversation_id=message.conversation_id,
                event_type=LATE_EVENT,
                payload={
                    "message_id": message.id,
                    "late_seconds": round((now - message.status_at).total_seconds()),  # type: ignore[operator]
                },
            )
        )
        message.processed_at = now
        message.processing_note = LATE_NOTE
        session.commit()
        logger.warning(
            "WhatsApp message %s of conversation %s arrived out of order (sent %s): "
            "not answered, left for the studio",
            message.id, message.conversation_id, message.status_at.isoformat(),  # type: ignore[union-attr]
        )  # fmt: skip

    def _mark_processed(self, message_id: int, note: str | None) -> None:
        with self.session_factory() as session:
            session.execute(
                update(WaMessage)
                .where(WaMessage.id == message_id)
                .values(processed_at=self._now(), processing_note=note)
            )
            session.commit()

    # --- Recovery after a restart ---------------------------------------------------------

    def recover_unanswered(self, max_age: timedelta) -> RecoveryResult:
        """See the module doc. Never raises."""
        result = RecoveryResult()
        try:
            self._recover(max_age, result)
        except Exception:
            logger.exception("Recovery of unanswered WhatsApp messages failed")
        return result

    def _recover(self, max_age: timedelta, result: RecoveryResult) -> None:
        now = self._now()
        with self.session_factory() as session:
            pending = session.execute(
                select(
                    WaMessage.id,
                    WaMessage.conversation_id,
                    WaMessage.created_at,
                    WaConversation.status,
                )  # fmt: skip
                .join(WaConversation, WaConversation.id == WaMessage.conversation_id)
                .where(
                    WaMessage.direction == WaDirection.INBOUND,
                    WaMessage.processed_at.is_(None),
                    or_(
                        WaMessage.processing_started_at.is_(None),
                        WaMessage.processing_started_at < now - CLAIM_TIMEOUT,
                    ),
                )
                .order_by(WaMessage.id)
            ).all()
            to_answer: list[int] = []
            for message_id, conversation_id, created_at, status in pending:
                if now - created_at > max_age:
                    if self._close(session, message_id, now, "stale_after_restart"):
                        result.stale.append(message_id)
                        logger.warning(
                            "WhatsApp message %s of conversation %s left unanswered: "
                            "%.0f minutes old after a restart",
                            message_id, conversation_id, (now - created_at).total_seconds() / 60,
                        )  # fmt: skip
                        session.add(
                            BotEvent(
                                conversation_id=conversation_id,
                                event_type=UNANSWERED_EVENT,
                                payload={"message_id": message_id, "reason": "stale"},
                            )
                        )
                elif status != WaConversationStatus.BOT:
                    if self._close(session, message_id, now, "not_with_bot"):
                        result.not_with_bot.append(message_id)
                else:
                    to_answer.append(message_id)
            session.commit()
        if pending:
            logger.info(
                "Recovery: %d message(s) to answer, %d too old, %d with a human",
                len(to_answer), len(result.stale), len(result.not_with_bot),
            )  # fmt: skip
        for message_id in to_answer:
            if self.process(message_id):
                result.answered.append(message_id)

    @staticmethod
    def _close(session: Session, message_id: int, now: datetime, note: str) -> bool:
        """Marks it processed unless someone else did meanwhile."""
        return (
            session.execute(
                update(WaMessage)
                .where(WaMessage.id == message_id, WaMessage.processed_at.is_(None))
                .values(processed_at=now, processing_note=note)
                .returning(WaMessage.id)
            ).scalar()
            is not None
        )
