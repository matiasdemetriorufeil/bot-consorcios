"""The panel's test chat ("Chat de prueba", app.admin.dev_chat): WhatsApp without a phone.
DEVELOPMENT ONLY (APP_ENV=development).

A message typed in the test chat is stored as if Meta had delivered it (store.record_incoming,
with an invented "sim." wamid) and answered by the same WhatsAppBot as the webhook's. Its
contact is marked simulated (wa_contacts.simulated): whatever goes to it, from the bot or from
an operator in "Conversaciones", is stored as usual but never reaches Meta (SimulatedSender).

DevClient is the WhatsApp client of the bot and of the inbox in development: simulated
contacts get the SimulatedSender, everyone else the real client (if WHATSAPP_ACCESS_TOKEN and
WHATSAPP_PHONE_NUMBER_ID are set; otherwise sending to them fails as "no configurado"). In
production the real client is used directly and nothing is ever simulated.
"""

import logging
import secrets
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.bot.choices import Choice, problems
from app.config import Settings
from app.db.models import WaContact, WaConversation, WaConversationStatus, WaMessage
from app.whatsapp import conversations, store
from app.whatsapp.client import WhatsAppClient, WhatsAppError
from app.whatsapp.events import WaIncoming

logger = logging.getLogger(__name__)

WAMID_PREFIX = "sim."
NOT_CONFIGURED = (
    "WhatsApp no está configurado (faltan WHATSAPP_ACCESS_TOKEN o WHATSAPP_PHONE_NUMBER_ID)"
)


def simulated_wamid() -> str:
    return WAMID_PREFIX + secrets.token_hex(12)


class SimulatedSender:
    """Accepts what the real client would (same checks of the options) and sends nothing."""

    def send_text(self, to: str, text: str) -> str:
        return simulated_wamid()

    def send_choices(self, to: str, text: str, choices: Sequence[Choice]) -> str:
        if found := problems(text, [c.title for c in choices]):
            raise ValueError("; ".join(found))
        return simulated_wamid()

    def send_template(
        self,
        to: str,
        name: str,
        language: str = "es_AR",
        components: list[dict[str, Any]] | None = None,
    ) -> str:
        return simulated_wamid()


class DevClient:
    """WhatsAppClient for development: simulated contacts never reach Meta."""

    def __init__(
        self,
        real: WhatsAppClient | None,
        session_factory: Callable[[], AbstractContextManager[Session]],
    ) -> None:
        self.real = real
        self.session_factory = session_factory
        self.simulator = SimulatedSender()

    def is_simulated(self, wa_id: str) -> bool:
        with self.session_factory() as session:
            return bool(
                session.scalar(
                    select(WaContact.simulated).where(WaContact.wa_id == wa_id.lstrip("+"))
                )
            )

    def _for(self, to: str) -> Any:
        if self.is_simulated(to):
            return self.simulator
        if self.real is None:
            raise WhatsAppError(NOT_CONFIGURED)
        return self.real

    def send_text(self, to: str, text: str) -> str:
        return self._for(to).send_text(to, text)

    def send_choices(self, to: str, text: str, choices: Sequence[Choice]) -> str:
        return self._for(to).send_choices(to, text, choices)

    def send_template(
        self,
        to: str,
        name: str,
        language: str = "es_AR",
        components: list[dict[str, Any]] | None = None,
    ) -> str:
        return self._for(to).send_template(to, name, language, components)

    def get_media(self, media_id: str) -> dict[str, Any]:
        if self.real is None:
            raise WhatsAppError(NOT_CONFIGURED)
        return self.real.get_media(media_id)

    def download(self, url: str, max_bytes: int) -> bytes:
        if self.real is None:
            raise WhatsAppError(NOT_CONFIGURED)
        return self.real.download(url, max_bytes)


def build_client(
    settings: Settings,
    session_factory: Callable[[], AbstractContextManager[Session]],
) -> WhatsAppClient | DevClient:
    """The WhatsApp client of the bot and the inbox. Production: the real one (WhatsAppError
    if it is not configured). Development: a DevClient, which works without Meta."""
    if settings.app_env != "development":
        return WhatsAppClient.from_settings(settings)
    try:
        real: WhatsAppClient | None = WhatsAppClient.from_settings(settings)
    except WhatsAppError:
        real = None
        logger.info("WhatsApp not configured: only the panel's test chat can be used")
    return DevClient(real, session_factory)


# --- The test chat's side -----------------------------------------------------------------------


def receive(
    session: Session,
    phone_e164: str,
    text: str,
    now: datetime,
    *,
    tapped: bool = False,
    profile_name: str | None = None,
) -> int:
    """Stores a message of the test chat as a delivery of Meta would (the caller commits and
    then has the bot process it). tapped: an option tapped (sent as its title, like Meta's
    button_reply). Returns the new message's id."""
    wa_id = phone_e164.lstrip("+")
    conversation = store.contact_conversation(session, wa_id, profile_name)
    # Before anything can be sent to it: from now on it never reaches Meta.
    session.execute(
        update(WaContact).where(WaContact.id == conversation.contact_id).values(simulated=True)
    )
    message = WaIncoming(
        wamid=simulated_wamid(),
        wa_id=wa_id,
        message_type="interactive" if tapped else "text",
        text=text,
        timestamp=now,
        profile_name=profile_name,
    )
    message_id = store.record_incoming(session, message, now)
    if message_id is None:  # pragma: no cover - the wamid is random
        raise LookupError("mensaje simulado duplicado")
    return message_id


def simulated_conversation(
    session: Session, phone_e164: str
) -> tuple[WaContact, WaConversation] | None:
    row = session.execute(
        select(WaContact, WaConversation)
        .join(WaConversation, WaConversation.contact_id == WaContact.id)
        .where(WaContact.phone_e164 == phone_e164, WaContact.simulated.is_(True))
    ).first()
    return (row[0], row[1]) if row else None


def restart(session: Session, phone_e164: str) -> bool:
    """Deletes the messages of a simulated conversation and gives it back to the bot, as if
    the contact had never written (only simulated contacts). The caller commits."""
    found = simulated_conversation(session, phone_e164)
    if found is None:
        return False
    _, conversation = found
    session.execute(delete(WaMessage).where(WaMessage.conversation_id == conversation.id))
    conversations.clear_handoff(conversation)
    conversation.status = WaConversationStatus.BOT
    conversation.assigned_to = None
    conversation.bot_since_message_id = None
    conversation.last_inbound_at = None
    conversation.resolved_at = None
    conversation.unread_count = 0
    return True
