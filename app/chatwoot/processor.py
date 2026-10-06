"""Answers one incoming Chatwoot message: app.channels.processor with the Chatwoot channel
(app.chatwoot.channel), which documents what is Chatwoot's own."""

import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime

from sqlalchemy.orm import Session

from app.bot.agent import Agent
from app.channels.locks import ConversationLock, no_lock
from app.channels.processor import (
    ATTACHMENT_REPLY,
    NON_PILOT_GREETING,
    UNSUPPORTED_REPLY,
    BotProcessor,
    in_pilot,
)
from app.chatwoot.channel import (
    SENT_POLL_SECONDS,
    SENT_WAIT_SECONDS,
    ChatwootChannel,
    resolve_phone,
    web_contact_phone,
)
from app.chatwoot.client import ChatwootClient
from app.chatwoot.events import IncomingMessage
from app.config import Settings

__all__ = [
    "ATTACHMENT_REPLY",
    "NON_PILOT_GREETING",
    "SENT_POLL_SECONDS",
    "SENT_WAIT_SECONDS",
    "UNSUPPORTED_REPLY",
    "ChatwootBot",
    "in_pilot",
    "resolve_phone",
    "web_contact_phone",
]


class ChatwootBot(BotProcessor):
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
        lock: ConversationLock = no_lock,
    ) -> None:
        self.client = client
        self.chatwoot = ChatwootChannel(
            client, settings.chatwoot_trusted_phone_inbox_ids, sleep=sleep, clock=clock
        )
        super().__init__(
            self.chatwoot,
            session_factory,
            agent_factory,
            settings,
            now=now,
            warm_up=warm_up,
            lock=lock,
        )

    def handle(self, message: IncomingMessage) -> None:  # type: ignore[override]
        """Entry point of the background task: never raises."""
        super().handle(self.chatwoot.to_inbound(message))
